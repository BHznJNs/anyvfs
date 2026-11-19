package memfs

import (
	"bytes"
	"context"
	"sync"
	"testing"
	"time"

	"github.com/winfsp/cgofuse/fuse"
	"anyvfs/native/internal/queue"
	pb "anyvfs/native/internal/rpc"
)

func newFS(t *testing.T, sensitive bool, file, total int64) (*FS, *queue.Queue[*pb.Notice]) {
	t.Helper()
	notices := queue.New[*pb.Notice]()
	f, err := New(&pb.Config{CaseSensitive: sensitive, MaxFileBytes: file, MaxTotalBytes: total}, nil, notices)
	if err != nil {
		t.Fatal(err)
	}
	return f, notices
}
func addInitialEntry(t *testing.T, f *FS, p string, data []byte, dir bool) {
	t.Helper()
	if err := f.AddInitialEntry(&pb.InitialEntry{Path: p, Data: data, Directory: dir}); err != nil {
		t.Fatal(err)
	}
}
func mustCode(t *testing.T, got, want int) {
	t.Helper()
	if got != want {
		t.Fatalf("code: got %d, want %d", got, want)
	}
}
func read(t *testing.T, f *FS, h uint64) []byte {
	t.Helper()
	buf := make([]byte, 2048)
	n := f.Read("ignored", buf, 0, h)
	if n < 0 {
		t.Fatal(n)
	}
	return buf[:n]
}

func drainNotices(t *testing.T, out *queue.Queue[*pb.Notice]) []*pb.Notice {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	out.Push(&pb.Notice{Value: &pb.Notice_Barrier{Barrier: &pb.Barrier{Id: 0}}})
	var notices []*pb.Notice
	for {
		n, err := out.Pop(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if n.GetBarrier() != nil {
			return notices
		}
		notices = append(notices, n)
	}
}

func TestHandlesSurviveRenameReplacementAndUnlink(t *testing.T) {
	f, out := newFS(t, true, 1024, 2048)
	addInitialEntry(t, f, "/a", []byte("original"), false)
	addInitialEntry(t, f, "/b", []byte("target"), false)
	c, a := f.Open("/a", fuse.O_RDWR)
	mustCode(t, c, 0)
	c, b := f.Open("/b", fuse.O_RDWR)
	mustCode(t, c, 0)
	mustCode(t, f.Rename("/a", "/b"), 0)
	if string(read(t, f, a)) != "original" || string(read(t, f, b)) != "target" {
		t.Fatal("replacement lost open inode")
	}
	mustCode(t, f.Unlink("/b"), 0)
	mustCode(t, f.Write("/b", []byte("!"), 8, a), 1)
	notices := drainNotices(t, out)
	event := notices[len(notices)-1].GetEvent().GetFileWritten()
	if event.Linked || event.Path != "/b" {
		t.Fatal("unlinked write must identify old inode and path")
	}
	if string(read(t, f, a)) != "original!" {
		t.Fatal("open inode did not survive unlink")
	}
	mustCode(t, f.Release("", a), 0)
	mustCode(t, f.Release("", b), 0)
	if f.used != 0 {
		t.Fatalf("leaked %d bytes", f.used)
	}
}
func TestSparseTruncateAppendAndEventCopy(t *testing.T) {
	f, out := newFS(t, true, 64, 64)
	c, h := f.Create("/a", fuse.O_RDWR, 0644)
	mustCode(t, c, 0)
	buf := []byte("ab")
	mustCode(t, f.Write("", buf, 3, h), 2)
	buf[0] = 'x'
	if !bytes.Equal(read(t, f, h), []byte{0, 0, 0, 'a', 'b'}) {
		t.Fatal("sparse gap is not zero-filled")
	}
	notices := drainNotices(t, out)
	if string(notices[1].GetEvent().GetFileWritten().Data) != "ab" {
		t.Fatal("event aliases caller buffer")
	}
	mustCode(t, f.Truncate("", 2, h), 0)
	mustCode(t, f.Truncate("", 5, h), 0)
	if !bytes.Equal(read(t, f, h), make([]byte, 5)) {
		t.Fatal("truncate exposed deleted data")
	}
	c, a := f.Open("/a", fuse.O_WRONLY|fuse.O_APPEND)
	mustCode(t, c, 0)
	mustCode(t, f.Write("", []byte("z"), 0, a), 1)
	notices = drainNotices(t, out)
	if notices[len(notices)-1].GetEvent().GetFileWritten().Offset != 5 {
		t.Fatal("wrong append offset")
	}
	mustCode(t, f.Read("", make([]byte, 1), 0, a), -fuse.EBADF)
}
func TestQuotasCountUnlinkedOpenFilesAndFailedOperations(t *testing.T) {
	f, _ := newFS(t, true, 8, 10)
	addInitialEntry(t, f, "/a", []byte("12345678"), false)
	c, a := f.Open("/a", fuse.O_RDWR)
	mustCode(t, c, 0)
	mustCode(t, f.Unlink("/a"), 0)
	c, b := f.Create("/b", fuse.O_RDWR, 0644)
	mustCode(t, c, 0)
	revision := f.revision
	mustCode(t, f.Write("", []byte("123"), 0, b), -fuse.ENOSPC)
	mustCode(t, f.Write("", []byte("123456789"), 0, b), -fuse.EFBIG)
	mustCode(t, f.Write("", []byte("x"), 1<<62, b), -fuse.EFBIG)
	mustCode(t, f.Truncate("", -1, b), -fuse.EINVAL)
	if f.revision != revision || len(read(t, f, b)) != 0 {
		t.Fatal("failed operation mutated state")
	}
	mustCode(t, f.Release("", a), 0)
	mustCode(t, f.Write("", []byte("123"), 0, b), 3)
}
func TestDirectoryRenameAndEnumerationSnapshot(t *testing.T) {
	f, out := newFS(t, false, 64, 128)
	addInitialEntry(t, f, "/Dir", nil, true)
	addInitialEntry(t, f, "/Dir/a", []byte("a"), false)
	c, h := f.Open("/dir/A", fuse.O_RDWR)
	mustCode(t, c, 0)
	c, d := f.Opendir("/Dir")
	mustCode(t, c, 0)
	mustCode(t, f.Rename("/Dir", "/dir"), 0)
	mustCode(t, f.Rename("/dir", "/New"), 0)
	mustCode(t, f.Write("", []byte("b"), 0, h), 1)
	notices := drainNotices(t, out)
	if notices[len(notices)-1].GetEvent().GetFileWritten().Path != "/New/a" {
		t.Fatal("subtree path not updated")
	}
	mustCode(t, f.Rename("/New", "/New/child"), -fuse.EINVAL)
	mustCode(t, f.Rmdir("/New"), -fuse.ENOTEMPTY)
	mustCode(t, f.Unlink("/New/a"), 0)
	var names []string
	mustCode(t, f.Readdir("", func(s string, _ *fuse.Stat_t, _ int64) bool { names = append(names, s); return true }, 2, d), 0)
	if len(names) != 1 || names[0] != "a" {
		t.Fatal("directory offset/snapshot changed")
	}
}
func TestConcurrentAppendAndBarrierOrdering(t *testing.T) {
	f, out := newFS(t, true, 1024, 1024)
	c, h := f.Create("/a", fuse.O_WRONLY|fuse.O_APPEND, 0644)
	mustCode(t, c, 0)
	var wg sync.WaitGroup
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if c := f.Write("", []byte("x"), 0, h); c != 1 {
				t.Errorf("write: %d", c)
			}
		}()
	}
	wg.Wait()
	f.Barrier(42)
	for i := uint64(1); i <= 101; i++ {
		n, err := out.Pop(t.Context())
		if err != nil {
			t.Fatal(err)
		}
		if n.GetEvent().Revision != i {
			t.Fatal("revision order violated")
		}
		if i > 1 && n.GetEvent().GetFileWritten().Offset != int64(i-2) {
			t.Fatal("append was not atomic")
		}
	}
	n, err := out.Pop(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	if n.GetBarrier().Id != 42 {
		t.Fatal("barrier overtook commits")
	}
}
func TestMetadataAndNoOpEvents(t *testing.T) {
	f, out := newFS(t, true, 64, 64)
	addInitialEntry(t, f, "/a", []byte("a"), false)
	c, h := f.Open("/a", fuse.O_RDONLY)
	mustCode(t, c, 0)
	mustCode(t, f.Flush("", h), 0)
	mustCode(t, f.Rename("/a", "/a"), 0)
	mustCode(t, f.Truncate("/a", 1, 0), 0)
	mustCode(t, f.Chmod("/a", 0644), 0)
	if f.revision != 0 {
		t.Fatal("no-op emitted event")
	}
	mustCode(t, f.Chmod("/a", 0600), 0)
	mustCode(t, f.Chown("/a", 123, 456), 0)
	mustCode(t, f.Utimens("/a", []fuse.Timespec{{Sec: 123, Nsec: 4}, {Sec: 567, Nsec: 8}}), 0)
	notices := drainNotices(t, out)
	if notices[0].GetEvent().GetModeChanged() == nil || notices[1].GetEvent().GetOwnerChanged() == nil ||
		notices[2].GetEvent().GetTimesChanged().MtimeNs != 567000000008 {
		t.Fatal("wrong metadata event")
	}
}

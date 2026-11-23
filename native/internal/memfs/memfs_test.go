package memfs

import (
	"bytes"
	"context"
	"errors"
	"sync"
	"testing"

	"anyvfs/native/internal/queue"
	pb "anyvfs/native/internal/rpc"
	"github.com/winfsp/cgofuse/fuse"
)

func newFS(t *testing.T, sensitive bool, file, total int64, entries ...*pb.BootstrapItem) (*FS, *queue.Queue[*pb.Notice]) {
	t.Helper()
	notices := queue.New[*pb.Notice]()
	f, err := New(&pb.Config{CaseSensitive: sensitive, MaxFileBytes: file, MaxTotalBytes: total}, entries, notices)
	if err != nil {
		t.Fatal(err)
	}
	return f, notices
}
func bootstrapFile(p, data string) *pb.BootstrapItem {
	return &pb.BootstrapItem{Value: &pb.BootstrapItem_File{
		File: &pb.InitialFile{Path: p, Data: []byte(data)},
	}}
}
func bootstrapDirectory(p string) *pb.BootstrapItem {
	return &pb.BootstrapItem{Value: &pb.BootstrapItem_Directory{
		Directory: &pb.InitialDirectory{Path: p},
	}}
}
func TestBootstrapImportsFilesAndDirectories(t *testing.T) {
	f, out := newFS(t, false, 64, 64,
		bootstrapFile("/docs/guides/a.bin", "\x00\xff"),
		bootstrapFile("/DOCS/empty.txt", ""),
		bootstrapDirectory("/inbox/pending"),
		bootstrapDirectory("/Docs"),
	)
	c, h := f.Open("/docs/guides/a.bin", fuse.O_RDONLY)
	mustCode(t, c, 0)
	if string(read(t, f, h)) != "\x00\xff" || f.used != 2 {
		t.Fatal("bootstrap content or quota usage is incorrect")
	}
	n, c := f.lookup("/docs/guides/a.bin")
	mustCode(t, c, 0)
	if got := n.location(); got != "/Docs/guides/a.bin" {
		t.Fatalf("got path %q, want explicit directory spelling", got)
	}
	n, c = f.lookup("/Docs/empty.txt")
	mustCode(t, c, 0)
	if n.dir() || len(n.data) != 0 {
		t.Fatal("expected a zero-byte file")
	}
	n, c = f.lookup("/inbox/pending")
	mustCode(t, c, 0)
	if !n.dir() || len(n.children) != 0 {
		t.Fatal("expected an empty directory")
	}
	if len(drainNotices(t, out)) != 0 {
		t.Fatal("bootstrap emitted a mutation notice")
	}
}

func TestBootstrapRejectsInvalidEntries(t *testing.T) {
	config := &pb.Config{MaxFileBytes: 2, MaxTotalBytes: 3}
	file := bootstrapFile("/Docs", "ab")
	nested := bootstrapFile("/docs/guide", "cd")
	directory := bootstrapDirectory("/docs")
	for _, tt := range []struct {
		name    string
		entries []*pb.BootstrapItem
	}{
		{"duplicate file", []*pb.BootstrapItem{file, bootstrapFile("/docs", "")}},
		{"duplicate directory", []*pb.BootstrapItem{nested, directory, bootstrapDirectory("/DOCS")}},
		{"file then explicit directory", []*pb.BootstrapItem{file, directory}},
		{"explicit directory then file", []*pb.BootstrapItem{directory, file}},
		{"file then child", []*pb.BootstrapItem{file, nested}},
		{"child then file", []*pb.BootstrapItem{nested, file}},
		{"trailing slash", []*pb.BootstrapItem{bootstrapFile("/docs/file/", "")}},
		{"repeated slash", []*pb.BootstrapItem{bootstrapFile("/docs//file", "")}},
		{"dot component", []*pb.BootstrapItem{bootstrapDirectory("/docs/../file")}},
		{"root", []*pb.BootstrapItem{bootstrapDirectory("/")}},
		{"single file quota", []*pb.BootstrapItem{bootstrapFile("/large", "abc")}},
		{"total quota", []*pb.BootstrapItem{file, bootstrapFile("/other", "cd")}},
		{"missing branch", []*pb.BootstrapItem{{}}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			f, err := New(config, tt.entries, queue.New[*pb.Notice]())
			if f != nil || err == nil {
				t.Fatal("expected bootstrap to fail")
			}
		})
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
	// Pop returns queued notices before checking cancellation.
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	var notices []*pb.Notice
	for {
		n, err := out.Pop(ctx)
		if errors.Is(err, context.Canceled) {
			return notices
		}
		if err != nil {
			t.Fatal(err)
		}
		notices = append(notices, n)
	}
}

func TestHandlesSurviveRenameReplacementAndUnlink(t *testing.T) {
	f, out := newFS(t, true, 1024, 2048, bootstrapFile("/a", "original"), bootstrapFile("/b", "target"))
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
	f, _ := newFS(t, true, 8, 10, bootstrapFile("/a", "12345678"))
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
	f, out := newFS(t, false, 64, 128, bootstrapDirectory("/Dir"), bootstrapFile("/Dir/a", "a"))
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
func TestConcurrentAppendAndEventOrdering(t *testing.T) {
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
	notices := drainNotices(t, out)
	if len(notices) != 101 {
		t.Fatalf("got %d notices, want 101", len(notices))
	}
	for i, notice := range notices {
		event := notice.GetEvent()
		if event.GetRevision() != uint64(i+1) {
			t.Fatal("revision order violated")
		}
		if i > 0 && event.GetFileWritten().Offset != int64(i-1) {
			t.Fatal("append was not atomic")
		}
	}
}
func TestMetadataAndNoOpEvents(t *testing.T) {
	f, out := newFS(t, true, 64, 64, bootstrapFile("/a", "a"))
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

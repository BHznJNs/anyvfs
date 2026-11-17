package bridge

import (
	"runtime"

	"vfspy/native/internal/memfs"
	"vfspy/native/internal/queue"
	pb "vfspy/native/internal/rpc"

	"github.com/winfsp/cgofuse/fuse"
)

type FuseHost struct {
	*fuse.FileSystemHost
	options []string
	notices *queue.Queue[*pb.Notice]
}

func newFuseHost(fs *memfs.FS, caseSensitive bool, notices *queue.Queue[*pb.Notice]) *FuseHost {
	host := fuse.NewFileSystemHost(fs)
	host.SetCapCaseInsensitive(!caseSensitive)
	host.SetCapReaddirPlus(true)
	host.SetCapOpenTrunc(true)

	options := []string{"-f", "-o", "volname=vfspy", "-o", "attr_timeout=0", "-o", "entry_timeout=0", "-o", "negative_timeout=0"}
	if runtime.GOOS == "windows" {
		options = append(options, "-o", "uid=-1,gid=-1")
	}
	return &FuseHost{FileSystemHost: host, options: options, notices: notices}
}

func (h *FuseHost) Mount(mountpoint string) bool {
	mounted := h.FileSystemHost.Mount(mountpoint, h.options)
	// Mount returns only after the callbacks are quiescent, so Stopped follows
	// every committed event, including when mounting fails.
	h.notices.Push(&pb.Notice{Value: &pb.Notice_Stopped{Stopped: &pb.Stopped{}}})
	return mounted
}

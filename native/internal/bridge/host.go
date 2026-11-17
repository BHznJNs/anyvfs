package bridge

import (
	"runtime"

	"vfspy/native/internal/memfs"

	"github.com/winfsp/cgofuse/fuse"
)

type FuseHost struct {
	*fuse.FileSystemHost
	options []string
}

func newFuseHost(fs *memfs.FS, caseSensitive bool) *FuseHost {
	host := fuse.NewFileSystemHost(fs)
	host.SetCapCaseInsensitive(!caseSensitive)
	host.SetCapReaddirPlus(true)
	host.SetCapOpenTrunc(true)

	options := []string{"-f", "-o", "volname=vfspy", "-o", "attr_timeout=0", "-o", "entry_timeout=0", "-o", "negative_timeout=0"}
	if runtime.GOOS == "windows" {
		options = append(options, "-o", "uid=-1,gid=-1")
	}
	return &FuseHost{FileSystemHost: host, options: options}
}

func (h *FuseHost) Mount(mountpoint string) bool {
	return h.FileSystemHost.Mount(mountpoint, h.options)
}

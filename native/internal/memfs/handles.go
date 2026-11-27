package memfs

import "github.com/winfsp/cgofuse/fuse"

type handle interface {
	openedNode() node
}

func (f *FS) registerHandle(h handle) uint64 {
	f.nextHandle++
	h.openedNode().metadata().refs++
	f.handles[f.nextHandle] = h
	return f.nextHandle
}

func (f *FS) Release(p string, fh uint64) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	h := f.handles[fh]
	if h == nil {
		return -fuse.EBADF
	}
	delete(f.handles, fh)
	n := h.openedNode()
	n.metadata().refs--
	f.reclaimUnlinkedFile(n)
	return 0
}

func (f *FS) Flush(p string, fh uint64) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.handles[fh] == nil {
		return -fuse.EBADF
	}
	return 0
}

func (f *FS) Fsync(p string, dataOnly bool, fh uint64) int    { return f.Flush(p, fh) }
func (f *FS) Fsyncdir(p string, dataOnly bool, fh uint64) int { return f.Flush(p, fh) }

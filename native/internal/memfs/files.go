package memfs

import (
	"github.com/winfsp/cgofuse/fuse"
	pb "anyvfs/native/internal/rpc"
)

func (f *FS) newHandle(n *node, flags int) uint64 {
	f.nextHandle++
	n.refs++
	f.handles[f.nextHandle] = &handle{n: n, flags: flags}
	return f.nextHandle
}
func (f *FS) create(p string, mode uint32, dir bool) (*node, int) {
	parent, name, c := f.parent(p)
	if c != 0 {
		return nil, c
	}
	if parent.children[f.key(name)] != nil {
		return nil, -fuse.EEXIST
	}
	kind := uint32(fuse.S_IFREG)
	if dir {
		kind = fuse.S_IFDIR
	}
	n := f.makeNode(parent, name, kind|mode&07777)
	f.attach(parent, n)
	if dir {
		f.commit(n, &pb.Event{Kind: &pb.Event_DirectoryCreated{DirectoryCreated: &pb.DirectoryCreated{Path: n.location(), Mode: n.stat.Mode}}})
	} else {
		f.commit(n, &pb.Event{Kind: &pb.Event_FileCreated{FileCreated: &pb.FileCreated{Path: n.location(), Mode: n.stat.Mode}}})
	}
	return n, 0
}
func (f *FS) Create(p string, flags int, mode uint32) (int, uint64) {
	f.mu.Lock()
	defer f.mu.Unlock()
	// Some FUSE implementations call Create for a non-exclusive existing file.
	if n, c := f.lookup(p); c == 0 {
		if flags&fuse.O_EXCL != 0 {
			return -fuse.EEXIST, 0
		}
		return f.open(n, flags)
	}
	n, c := f.create(p, mode, false)
	if c != 0 {
		return c, 0
	}
	return 0, f.newHandle(n, flags)
}
func (f *FS) Mkdir(p string, mode uint32) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	_, c := f.create(p, mode, true)
	return c
}
func (f *FS) Mknod(p string, mode uint32, dev uint64) int {
	if mode&fuse.S_IFMT != fuse.S_IFREG {
		return -fuse.ENOSYS
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	_, c := f.create(p, mode, false)
	return c
}
func (f *FS) open(n *node, flags int) (int, uint64) {
	if n.dir() {
		return -fuse.EISDIR, 0
	}
	if flags&fuse.O_TRUNC != 0 {
		if flags&fuse.O_ACCMODE == fuse.O_RDONLY {
			return -fuse.EACCES, 0
		}
		if c := f.truncate(n, 0); c != 0 {
			return c, 0
		}
	}
	return 0, f.newHandle(n, flags)
}
func (f *FS) Open(p string, flags int) (int, uint64) {
	f.mu.Lock()
	defer f.mu.Unlock()
	n, c := f.lookup(p)
	if c != 0 {
		return c, 0
	}
	return f.open(n, flags)
}
func (f *FS) Read(p string, buf []byte, offset int64, fh uint64) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	h := f.handles[fh]
	if h == nil || h.flags&fuse.O_ACCMODE == fuse.O_WRONLY {
		return -fuse.EBADF
	}
	if h.n.dir() {
		return -fuse.EISDIR
	}
	if offset < 0 {
		return -fuse.EINVAL
	}
	if offset >= int64(len(h.n.data)) {
		return 0
	}
	return copy(buf, h.n.data[int(offset):])
}
func (f *FS) Write(p string, buf []byte, offset int64, fh uint64) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	h := f.handles[fh]
	if h == nil || h.flags&fuse.O_ACCMODE == fuse.O_RDONLY {
		return -fuse.EBADF
	}
	n := h.n
	if n.dir() {
		return -fuse.EISDIR
	}
	if offset < 0 {
		return -fuse.EINVAL
	}
	if h.flags&fuse.O_APPEND != 0 {
		offset = int64(len(n.data))
	}
	if int64(len(buf)) > f.config.MaxFileBytes || offset > f.config.MaxFileBytes-int64(len(buf)) {
		return -fuse.EFBIG
	}
	if len(buf) == 0 {
		return 0
	}
	end := offset + int64(len(buf))
	if end > int64(len(n.data)) {
		if c := f.resize(n, end); c != 0 {
			return c
		}
	}
	copy(n.data[int(offset):], buf)
	f.touch(n)
	f.commit(n, &pb.Event{Kind: &pb.Event_FileWritten{FileWritten: &pb.FileWritten{
		Path: n.location(), Offset: offset, Data: append([]byte(nil), buf...), Size: n.stat.Size, Linked: n.linked}}})
	return len(buf)
}
func (f *FS) truncate(n *node, size int64) int {
	if n.dir() {
		return -fuse.EISDIR
	}
	old := int64(len(n.data))
	if size == old {
		return 0
	}
	if c := f.resize(n, size); c != 0 {
		return c
	}
	f.touch(n)
	f.commit(n, &pb.Event{Kind: &pb.Event_FileTruncated{FileTruncated: &pb.FileTruncated{
		Path: n.location(), PreviousSize: old, Size: size, Linked: n.linked}}})
	return 0
}
func (f *FS) Truncate(p string, size int64, fh uint64) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	if fh != 0 && fh != ^uint64(0) {
		h := f.handles[fh]
		if h == nil || h.flags&fuse.O_ACCMODE == fuse.O_RDONLY {
			return -fuse.EBADF
		}
	}
	n, c := f.target(p, fh)
	if c != 0 {
		return c
	}
	return f.truncate(n, size)
}
func (f *FS) Release(p string, fh uint64) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	h := f.handles[fh]
	if h == nil {
		return -fuse.EBADF
	}
	delete(f.handles, fh)
	h.n.refs--
	if !h.n.linked && h.n.refs == 0 {
		f.used -= int64(len(h.n.data))
		h.n.data = nil
	}
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

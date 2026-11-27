package memfs

import (
	pb "anyvfs/native/internal/rpc"

	"github.com/winfsp/cgofuse/fuse"
)

type fileNode struct {
	nodeMeta
	data []byte
}

func (f *FS) newFileNode(parent *dirNode, name string, mode uint32) *fileNode {
	return &fileNode{nodeMeta: f.makeNodeMeta(parent, name, fuse.S_IFREG|mode)}
}

type fileHandle struct {
	file  *fileNode
	flags int
}

func (h *fileHandle) openedNode() node { return h.file }

func (f *FS) createFile(p string, mode uint32) (*fileNode, int) {
	parent, name, c := f.creationParent(p)
	if c != 0 {
		return nil, c
	}
	n := f.newFileNode(parent, name, mode&07777)
	f.attach(parent, n)
	f.commit(n, &pb.Event{Kind: &pb.Event_FileCreated{FileCreated: &pb.FileCreated{Path: n.location(), Mode: n.stat.Mode}}})
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
	n, c := f.createFile(p, mode)
	if c != 0 {
		return c, 0
	}
	return 0, f.registerHandle(&fileHandle{file: n, flags: flags})
}
func (f *FS) Mknod(p string, mode uint32, dev uint64) int {
	if mode&fuse.S_IFMT != fuse.S_IFREG {
		return -fuse.ENOSYS
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	_, c := f.createFile(p, mode)
	return c
}
func (f *FS) open(n node, flags int) (int, uint64) {
	file, ok := n.(*fileNode)
	if !ok {
		return -fuse.EISDIR, 0
	}
	if flags&fuse.O_TRUNC != 0 {
		if flags&fuse.O_ACCMODE == fuse.O_RDONLY {
			return -fuse.EACCES, 0
		}
		if c := f.truncate(file, 0); c != 0 {
			return c, 0
		}
	}
	return 0, f.registerHandle(&fileHandle{file: file, flags: flags})
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
	opened := f.handles[fh]
	if _, ok := opened.(*dirHandle); ok {
		return -fuse.EISDIR
	}
	h, ok := opened.(*fileHandle)
	if !ok || h.flags&fuse.O_ACCMODE == fuse.O_WRONLY {
		return -fuse.EBADF
	}
	if offset < 0 {
		return -fuse.EINVAL
	}
	if offset >= int64(len(h.file.data)) {
		return 0
	}
	return copy(buf, h.file.data[int(offset):])
}
func (f *FS) Write(p string, buf []byte, offset int64, fh uint64) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	h, ok := f.handles[fh].(*fileHandle)
	if !ok || h.flags&fuse.O_ACCMODE == fuse.O_RDONLY {
		return -fuse.EBADF
	}
	n := h.file
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
func (f *FS) truncate(n *fileNode, size int64) int {
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
		h, ok := f.handles[fh].(*fileHandle)
		if !ok || h.flags&fuse.O_ACCMODE == fuse.O_RDONLY {
			return -fuse.EBADF
		}
		return f.truncate(h.file, size)
	}
	n, c := f.lookup(p)
	if c != 0 {
		return c
	}
	file, ok := n.(*fileNode)
	if !ok {
		return -fuse.EISDIR
	}
	return f.truncate(file, size)
}
func (f *FS) resize(n *fileNode, size int64) int {
	if size < 0 {
		return -fuse.EINVAL
	}
	if size > f.config.MaxFileBytes {
		return -fuse.EFBIG
	}
	delta := size - int64(len(n.data))
	if delta > f.config.MaxTotalBytes-f.used {
		return -fuse.ENOSPC
	}
	if delta > 0 {
		n.data = append(n.data, make([]byte, int(delta))...)
	} else if delta < 0 {
		// Release backing storage too, so shrinking cannot hide old allocations.
		n.data = append([]byte(nil), n.data[:int(size)]...)
	}
	f.used += delta
	n.stat.Size = size
	n.stat.Blocks = (size + 511) / 512
	return 0
}

func (f *FS) reclaimUnlinkedFile(n node) {
	if file, ok := n.(*fileNode); ok && !file.linked && file.refs == 0 {
		f.used -= int64(len(file.data))
		file.data = nil
	}
}

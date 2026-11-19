package memfs

import (
	"github.com/winfsp/cgofuse/fuse"
	"sort"
	pb "anyvfs/native/internal/rpc"
)

func (f *FS) Opendir(p string) (int, uint64) {
	f.mu.Lock()
	defer f.mu.Unlock()
	n, c := f.lookup(p)
	if c != 0 {
		return c, 0
	}
	if !n.dir() {
		return -fuse.ENOTDIR, 0
	}
	fh := f.newHandle(n, fuse.O_RDONLY)
	h := f.handles[fh]
	parent := n.parent
	if parent == nil {
		parent = n
	}
	h.entries = []directoryEntry{{".", n.stat}, {"..", parent.stat}}
	names := make([]string, 0, len(n.children))
	for name := range n.children {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		child := n.children[name]
		h.entries = append(h.entries, directoryEntry{child.name, child.stat})
	}
	return 0, fh
}
func (f *FS) Readdir(p string, fill func(string, *fuse.Stat_t, int64) bool, offset int64, fh uint64) int {
	f.mu.Lock()
	h := f.handles[fh]
	if h == nil || !h.n.dir() {
		f.mu.Unlock()
		return -fuse.EBADF
	}
	entries := h.entries
	f.mu.Unlock()
	if offset < 0 {
		return -fuse.EINVAL
	}
	for i := offset; i < int64(len(entries)); i++ {
		e := entries[i]
		if !fill(e.name, &e.stat, i+1) {
			break
		}
	}
	return 0
}
func (f *FS) Releasedir(p string, fh uint64) int { return f.Release(p, fh) }
func (f *FS) Unlink(p string) int                { return f.remove(p, false) }
func (f *FS) Rmdir(p string) int                 { return f.remove(p, true) }
func (f *FS) remove(p string, dir bool) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n, c := f.lookup(p)
	if c != 0 {
		return c
	}
	if n == f.root {
		return -fuse.EBUSY
	}
	if dir && !n.dir() {
		return -fuse.ENOTDIR
	}
	if !dir && n.dir() {
		return -fuse.EISDIR
	}
	if len(n.children) > 0 {
		return -fuse.ENOTEMPTY
	}
	location := n.location()
	f.detach(n)
	if dir {
		f.commit(n, &pb.Event{Kind: &pb.Event_DirectoryRemoved{DirectoryRemoved: &pb.DirectoryRemoved{Path: location}}})
	} else {
		f.commit(n, &pb.Event{Kind: &pb.Event_FileRemoved{FileRemoved: &pb.FileRemoved{Path: location}}})
	}
	return 0
}
func (f *FS) Rename(old, new string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n, c := f.lookup(old)
	if c != 0 {
		return c
	}
	if n == f.root {
		return -fuse.EBUSY
	}
	parent, name, c := f.parent(new)
	if c != 0 {
		return c
	}
	for ancestor := parent; ancestor != nil; ancestor = ancestor.parent {
		if ancestor == n {
			return -fuse.EINVAL
		}
	}
	dest := parent.children[f.key(name)]
	replaced := uint64(0)
	if dest == n && n.name == name && n.parent == parent {
		return 0
	}
	if dest != nil && dest != n {
		if dest.dir() && !n.dir() {
			return -fuse.EISDIR
		}
		if !dest.dir() && n.dir() {
			return -fuse.ENOTDIR
		}
		if len(dest.children) > 0 {
			return -fuse.ENOTEMPTY
		}
		replaced = dest.stat.Ino
	}
	oldPath := n.location()
	if replaced != 0 {
		f.detach(dest)
	}
	delete(n.parent.children, f.key(n.name))
	if n.dir() {
		n.parent.stat.Nlink--
	}
	f.touch(n.parent)
	n.name = name
	n.parent = parent
	f.attach(parent, n)
	n.stat.Ctim = fuse.Now()
	f.commit(n, &pb.Event{Kind: &pb.Event_EntryRenamed{EntryRenamed: &pb.EntryRenamed{
		Path: oldPath, NewPath: n.location(), Directory: n.dir(), ReplacedInode: replaced}}})
	return 0
}

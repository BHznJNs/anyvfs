package memfs

import (
	"sort"

	pb "anyvfs/native/internal/rpc"

	"github.com/winfsp/cgofuse/fuse"
)

type dirNode struct {
	nodeMeta
	children map[string]node
}

func (f *FS) newDirNode(parent *dirNode, name string, mode uint32) *dirNode {
	meta := f.makeNodeMeta(parent, name, fuse.S_IFDIR|mode)
	meta.stat.Nlink = 2
	return &dirNode{nodeMeta: meta, children: make(map[string]node)}
}

type directoryEntry struct {
	name string
	stat fuse.Stat_t
}

type dirHandle struct {
	dir     *dirNode
	entries []directoryEntry
}

func (h *dirHandle) openedNode() node { return h.dir }

func (f *FS) Mkdir(p string, mode uint32) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	parent, name, code := f.creationParent(p)
	if code != 0 {
		return code
	}
	dir := f.newDirNode(parent, name, mode&07777)
	f.attach(parent, dir)
	f.commit(dir, &pb.Event{Kind: &pb.Event_DirectoryCreated{DirectoryCreated: &pb.DirectoryCreated{Path: dir.location(), Mode: dir.stat.Mode}}})
	return 0
}

func (f *FS) Opendir(p string) (int, uint64) {
	f.mu.Lock()
	defer f.mu.Unlock()
	n, c := f.lookup(p)
	if c != 0 {
		return c, 0
	}
	dir, ok := n.(*dirNode)
	if !ok {
		return -fuse.ENOTDIR, 0
	}
	parent := dir.parent
	if parent == nil {
		parent = dir
	}
	entries := []directoryEntry{{".", dir.stat}, {"..", parent.stat}}
	names := make([]string, 0, len(dir.children))
	for name := range dir.children {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		meta := dir.children[name].metadata()
		entries = append(entries, directoryEntry{meta.name, meta.stat})
	}
	return 0, f.registerHandle(&dirHandle{dir: dir, entries: entries})
}
func (f *FS) Readdir(p string, fill func(string, *fuse.Stat_t, int64) bool, offset int64, fh uint64) int {
	f.mu.Lock()
	h, ok := f.handles[fh].(*dirHandle)
	if !ok {
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
	directory, isDir := n.(*dirNode)
	if dir && !isDir {
		return -fuse.ENOTDIR
	}
	if !dir && isDir {
		return -fuse.EISDIR
	}
	if isDir && len(directory.children) > 0 {
		return -fuse.ENOTEMPTY
	}
	location := n.metadata().location()
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
	meta := n.metadata()
	_, isDir := n.(*dirNode)
	if dest == n && meta.name == name && meta.parent == parent {
		return 0
	}
	if dest != nil && dest != n {
		destDir, destIsDir := dest.(*dirNode)
		if destIsDir && !isDir {
			return -fuse.EISDIR
		}
		if !destIsDir && isDir {
			return -fuse.ENOTDIR
		}
		if destIsDir && len(destDir.children) > 0 {
			return -fuse.ENOTEMPTY
		}
		replaced = dest.metadata().stat.Ino
	}
	oldPath := meta.location()
	if replaced != 0 {
		f.detach(dest)
	}
	f.removeChild(n)
	meta.name = name
	meta.parent = parent
	f.attach(parent, n)
	meta.stat.Ctim = fuse.Now()
	f.commit(n, &pb.Event{Kind: &pb.Event_EntryRenamed{EntryRenamed: &pb.EntryRenamed{
		Path: oldPath, NewPath: meta.location(), Directory: isDir, ReplacedInode: replaced}}})
	return 0
}

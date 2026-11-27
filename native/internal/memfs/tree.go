// Package memfs owns all live filesystem data, handles and mutation ordering.
package memfs

import (
	"fmt"
	"path"
	"runtime"
	"strings"
	"sync"
	"time"

	"anyvfs/native/internal/queue"
	pb "anyvfs/native/internal/rpc"

	"github.com/winfsp/cgofuse/fuse"
)

type node interface {
	metadata() *nodeMeta
}

type nodeMeta struct {
	name   string
	parent *dirNode
	stat   fuse.Stat_t
	refs   int
	linked bool
}

func (n *nodeMeta) metadata() *nodeMeta { return n }

// location returns the current path, or an empty string if the node is unlinked.
func (n *nodeMeta) location() string {
	if !n.linked {
		return ""
	}
	if n.parent == nil {
		return "/"
	}
	return path.Join(n.parent.location(), n.name)
}

type FS struct {
	fuse.FileSystemBase
	mu                              sync.Mutex
	root                            *dirNode
	handles                         map[uint64]handle
	nextInode, nextHandle, revision uint64
	used                            int64
	config                          *pb.Config
	notices                         *queue.Queue[*pb.Notice]
	OnInit                          func()
}

func New(config *pb.Config, entries []*pb.BootstrapItem, notices *queue.Queue[*pb.Notice]) (*FS, error) {
	if config == nil || config.MaxFileBytes <= 0 || config.MaxTotalBytes <= 0 ||
		config.MaxFileBytes > config.MaxTotalBytes || config.MaxFileBytes > 1<<30 || config.MaxTotalBytes > 1<<40 {
		return nil, fmt.Errorf("invalid memory limits (require 0 < file <= total, file <= 1 GiB, total <= 1 TiB)")
	}
	if notices == nil {
		return nil, fmt.Errorf("nil notice queue")
	}
	f := &FS{config: config, handles: make(map[uint64]handle), notices: notices}
	f.root = f.newDirNode(nil, "", 0755)
	if err := f.importInitialEntries(entries); err != nil {
		return nil, err
	}
	return f, nil
}
func (f *FS) key(s string) string {
	if f.config.CaseSensitive {
		return s
	}
	return strings.ToLower(s)
}
func (f *FS) makeNodeMeta(parent *dirNode, name string, mode uint32) nodeMeta {
	f.nextInode++
	now := fuse.NewTimespec(time.Now())
	n := nodeMeta{name: name, parent: parent, linked: true, stat: fuse.Stat_t{
		Ino: f.nextInode, Mode: mode, Nlink: 1, Atim: now, Mtim: now, Ctim: now, Birthtim: now, Blksize: 4096}}
	if runtime.GOOS != "windows" {
		n.stat.Uid, n.stat.Gid = uint32(osUID()), uint32(osGID())
	}
	return n
}
func validName(s string) bool {
	return s != "" && s != "." && s != ".." && len(s) <= 255 &&
		!strings.ContainsAny(s, "\\:\x00<>\"|?*") && !strings.HasSuffix(s, ".") && !strings.HasSuffix(s, " ")
}
func (f *FS) lookup(p string) (node, int) {
	if p == "/" {
		return f.root, 0
	}
	if !strings.HasPrefix(p, "/") || strings.HasSuffix(p, "/") {
		return nil, -fuse.EINVAL
	}
	var n node = f.root
	for _, part := range strings.Split(p[1:], "/") {
		if !validName(part) {
			return nil, -fuse.EINVAL
		}
		dir, ok := n.(*dirNode)
		if !ok {
			return nil, -fuse.ENOTDIR
		}
		n = dir.children[f.key(part)]
		if n == nil {
			return nil, -fuse.ENOENT
		}
	}
	return n, 0
}
func (f *FS) parent(p string) (*dirNode, string, int) {
	if p == "/" {
		return nil, "", -fuse.EBUSY
	}
	name := path.Base(p)
	if !validName(name) {
		return nil, "", -fuse.EINVAL
	}
	n, c := f.lookup(path.Dir(p))
	if c != 0 {
		return nil, "", c
	}
	dir, ok := n.(*dirNode)
	if !ok {
		return nil, "", -fuse.ENOTDIR
	}
	return dir, name, 0
}

func (f *FS) creationParent(p string) (*dirNode, string, int) {
	parent, name, code := f.parent(p)
	if code != 0 {
		return nil, "", code
	}
	if parent.children[f.key(name)] != nil {
		return nil, "", -fuse.EEXIST
	}
	return parent, name, 0
}

func (f *FS) target(p string, fh uint64) (node, int) {
	if fh != 0 && fh != ^uint64(0) {
		h := f.handles[fh]
		if h == nil {
			return nil, -fuse.EBADF
		}
		return h.openedNode(), 0
	}
	return f.lookup(p)
}
func (f *FS) touch(n node) {
	now := fuse.NewTimespec(time.Now())
	meta := n.metadata()
	meta.stat.Mtim = now
	meta.stat.Ctim = now
}
func (f *FS) attach(parent *dirNode, n node) {
	parent.children[f.key(n.metadata().name)] = n
	if _, ok := n.(*dirNode); ok {
		parent.stat.Nlink++
	}
	f.touch(parent)
}
func (f *FS) detach(n node) {
	meta := n.metadata()
	f.removeChild(n)
	meta.linked = false
	meta.stat.Nlink = 0
	f.reclaimUnlinkedFile(n)
}

// removeChild unlinks a name from its parent without changing the node's identity.
func (f *FS) removeChild(n node) {
	meta := n.metadata()
	parent := meta.parent
	delete(parent.children, f.key(meta.name))
	if _, ok := n.(*dirNode); ok {
		parent.stat.Nlink--
	}
	f.touch(parent)
}
func (f *FS) commit(n node, e *pb.Event) {
	f.revision++
	e.Revision = f.revision
	e.Inode = n.metadata().stat.Ino
	e.TimeNs = time.Now().UnixNano()
	f.notices.Push(&pb.Notice{Value: &pb.Notice_Event{Event: e}})
}

func (f *FS) Init() {
	f.mu.Lock()
	f.notices.Push(&pb.Notice{Value: &pb.Notice_Ready{Ready: &pb.Ready{}}})
	f.mu.Unlock()
	if f.OnInit != nil {
		f.OnInit()
	}
}

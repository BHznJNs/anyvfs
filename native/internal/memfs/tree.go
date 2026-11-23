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

type node struct {
	name        string
	parent      *node
	children    map[string]*node
	data        []byte
	stat        fuse.Stat_t
	refs        int
	linked      bool
	removedPath string
}

func (n *node) dir() bool { return n.children != nil }
func (n *node) location() string {
	if !n.linked {
		return n.removedPath
	}
	if n.parent == nil {
		return "/"
	}
	return path.Join(n.parent.location(), n.name)
}

type directoryEntry struct {
	name string
	stat fuse.Stat_t
}
type handle struct {
	n       *node
	flags   int
	entries []directoryEntry
}

type FS struct {
	fuse.FileSystemBase
	mu                              sync.Mutex
	root                            *node
	handles                         map[uint64]*handle
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
	f := &FS{config: config, handles: make(map[uint64]*handle), notices: notices}
	f.root = f.makeNode(nil, "", fuse.S_IFDIR|0755)
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
func (f *FS) makeNode(parent *node, name string, mode uint32) *node {
	f.nextInode++
	now := fuse.NewTimespec(time.Now())
	n := &node{name: name, parent: parent, linked: true, stat: fuse.Stat_t{
		Ino: f.nextInode, Mode: mode, Nlink: 1, Atim: now, Mtim: now, Ctim: now, Birthtim: now, Blksize: 4096}}
	if runtime.GOOS != "windows" {
		n.stat.Uid, n.stat.Gid = uint32(osUID()), uint32(osGID())
	}
	if mode&fuse.S_IFMT == fuse.S_IFDIR {
		n.children = make(map[string]*node)
		n.stat.Nlink = 2
	}
	return n
}
func validName(s string) bool {
	return s != "" && s != "." && s != ".." && len(s) <= 255 &&
		!strings.ContainsAny(s, "\\:\x00<>\"|?*") && !strings.HasSuffix(s, ".") && !strings.HasSuffix(s, " ")
}
func (f *FS) lookup(p string) (*node, int) {
	if p == "/" {
		return f.root, 0
	}
	if !strings.HasPrefix(p, "/") || strings.HasSuffix(p, "/") {
		return nil, -fuse.EINVAL
	}
	n := f.root
	for _, part := range strings.Split(p[1:], "/") {
		if !validName(part) {
			return nil, -fuse.EINVAL
		}
		if !n.dir() {
			return nil, -fuse.ENOTDIR
		}
		n = n.children[f.key(part)]
		if n == nil {
			return nil, -fuse.ENOENT
		}
	}
	return n, 0
}
func (f *FS) parent(p string) (*node, string, int) {
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
	if !n.dir() {
		return nil, "", -fuse.ENOTDIR
	}
	return n, name, 0
}
func (f *FS) target(p string, fh uint64) (*node, int) {
	if fh != 0 && fh != ^uint64(0) {
		h := f.handles[fh]
		if h == nil {
			return nil, -fuse.EBADF
		}
		return h.n, 0
	}
	return f.lookup(p)
}
func (f *FS) touch(n *node) {
	now := fuse.NewTimespec(time.Now())
	n.stat.Mtim = now
	n.stat.Ctim = now
}
func (f *FS) attach(parent, n *node) {
	parent.children[f.key(n.name)] = n
	if n.dir() {
		parent.stat.Nlink++
	}
	f.touch(parent)
}
func (f *FS) detach(n *node) {
	n.removedPath = n.location()
	delete(n.parent.children, f.key(n.name))
	if n.dir() {
		n.parent.stat.Nlink--
	}
	f.touch(n.parent)
	n.linked = false
	n.stat.Nlink = 0
	if n.refs == 0 {
		f.used -= int64(len(n.data))
		n.data = nil
	}
}
func (f *FS) resize(n *node, size int64) int {
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
func (f *FS) commit(n *node, e *pb.Event) {
	f.revision++
	e.Revision = f.revision
	e.Inode = n.stat.Ino
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

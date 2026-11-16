package memfs

import (
	"github.com/winfsp/cgofuse/fuse"
	"time"
	pb "vfspy/native/internal/rpc"
)

func (f *FS) Getattr(p string, st *fuse.Stat_t, fh uint64) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n, c := f.target(p, fh)
	if c == 0 {
		*st = n.stat
	}
	return c
}
func (f *FS) Statfs(p string, st *fuse.Statfs_t) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	free := (f.config.MaxTotalBytes - f.used) / 4096
	*st = fuse.Statfs_t{Bsize: 4096, Frsize: 4096, Blocks: uint64(f.config.MaxTotalBytes / 4096),
		Bfree: uint64(free), Bavail: uint64(free), Files: f.nextInode, Ffree: 1000000, Namemax: 255}
	return 0
}
func (f *FS) Access(p string, mask uint32) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n, c := f.lookup(p)
	if c != 0 {
		return c
	}
	// A single mounting user; no impersonation or ACL emulation.
	if (n.stat.Mode>>6)&mask != mask {
		return -fuse.EACCES
	}
	return 0
}
func (f *FS) Chmod(p string, mode uint32) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n, c := f.lookup(p)
	if c != 0 {
		return c
	}
	mode = n.stat.Mode&fuse.S_IFMT | mode&07777
	if mode == n.stat.Mode {
		return 0
	}
	n.stat.Mode = mode
	n.stat.Ctim = fuse.Now()
	f.commit(n, &pb.Event{Kind: &pb.Event_ModeChanged{ModeChanged: &pb.ModeChanged{Path: n.location(), Mode: mode}}})
	return 0
}
func (f *FS) Chown(p string, uid, gid uint32) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n, c := f.lookup(p)
	if c != 0 {
		return c
	}
	if uid == ^uint32(0) {
		uid = n.stat.Uid
	}
	if gid == ^uint32(0) {
		gid = n.stat.Gid
	}
	if uid == n.stat.Uid && gid == n.stat.Gid {
		return 0
	}
	n.stat.Uid = uid
	n.stat.Gid = gid
	n.stat.Ctim = fuse.Now()
	f.commit(n, &pb.Event{Kind: &pb.Event_OwnerChanged{OwnerChanged: &pb.OwnerChanged{Path: n.location(), Uid: uid, Gid: gid}}})
	return 0
}
func (f *FS) Utimens(p string, times []fuse.Timespec) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n, c := f.lookup(p)
	if c != 0 {
		return c
	}
	now := fuse.NewTimespec(time.Now())
	at, mt := now, now
	if len(times) != 0 && len(times) != 2 {
		return -fuse.EINVAL
	}
	if len(times) == 2 {
		at, mt = times[0], times[1]
		convert := func(t, old fuse.Timespec) (fuse.Timespec, bool) {
			if t.Nsec == int64(fuse.UTIME_NOW) {
				return now, true
			}
			if t.Nsec == int64(fuse.UTIME_OMIT) {
				return old, true
			}
			return t, t.Nsec >= 0 && t.Nsec < 1e9
		}
		var ok bool
		if at, ok = convert(at, n.stat.Atim); !ok {
			return -fuse.EINVAL
		}
		if mt, ok = convert(mt, n.stat.Mtim); !ok {
			return -fuse.EINVAL
		}
	}
	if at == n.stat.Atim && mt == n.stat.Mtim {
		return 0
	}
	n.stat.Atim = at
	n.stat.Mtim = mt
	n.stat.Ctim = now
	f.commit(n, &pb.Event{Kind: &pb.Event_TimesChanged{TimesChanged: &pb.TimesChanged{
		Path: n.location(), AtimeNs: at.Sec*1e9 + at.Nsec, MtimeNs: mt.Sec*1e9 + mt.Nsec}}})
	return 0
}

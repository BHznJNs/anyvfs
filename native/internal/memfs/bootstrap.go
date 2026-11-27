package memfs

import (
	"fmt"
	"strings"

	pb "anyvfs/native/internal/rpc"
)

// importInitialEntries builds the tree before mounting and emits no mutation events.
func (f *FS) importInitialEntries(entries []*pb.BootstrapItem) error {
	declaredDirectories := make(map[*dirNode]struct{})
	for _, entry := range entries {
		switch value := entry.GetValue().(type) {
		case *pb.BootstrapItem_File:
			if err := f.importInitialFile(value.File); err != nil {
				return err
			}
		case *pb.BootstrapItem_Directory:
			p := value.Directory.GetPath()
			parent, name, err := f.initialParent(p)
			if err != nil {
				return err
			}
			dir := f.ensureInitialDirectory(parent, name)
			if dir == nil {
				return fmt.Errorf("initial directory %q conflicts with a file", p)
			}
			if _, declared := declaredDirectories[dir]; declared {
				return fmt.Errorf("duplicate initial directory %q", p)
			}
			// An explicit declaration determines the spelling of an inferred directory.
			dir.name = name
			declaredDirectories[dir] = struct{}{}
		default:
			return fmt.Errorf("expected an initial file or directory in bootstrap stream")
		}
	}
	return nil
}

func (f *FS) importInitialFile(file *pb.InitialFile) error {
	p := file.GetPath()
	parent, name, err := f.initialParent(p)
	if err != nil {
		return err
	}
	if parent.children[f.key(name)] != nil {
		return fmt.Errorf("initial file path %q already exists", p)
	}
	n := f.newFileNode(parent, name, 0644)
	data := file.GetData()
	if code := f.resize(n, int64(len(data))); code != 0 {
		return fmt.Errorf("initial file %q exceeds memory limits: %d", p, code)
	}
	copy(n.data, data)
	f.attach(parent, n)
	return nil
}

func (f *FS) initialParent(p string) (*dirNode, string, error) {
	if !strings.HasPrefix(p, "/") {
		return nil, "", fmt.Errorf("invalid initial path %q", p)
	}
	parts := strings.Split(p[1:], "/")
	for _, part := range parts {
		if !validName(part) {
			return nil, "", fmt.Errorf("invalid initial path %q", p)
		}
	}
	parent := f.root
	for _, part := range parts[:len(parts)-1] {
		parent = f.ensureInitialDirectory(parent, part)
		if parent == nil {
			return nil, "", fmt.Errorf("initial path %q has a file as a parent", p)
		}
	}
	return parent, parts[len(parts)-1], nil
}

// ensureInitialDirectory returns nil when a file already occupies the name.
func (f *FS) ensureInitialDirectory(parent *dirNode, name string) *dirNode {
	if existing := parent.children[f.key(name)]; existing != nil {
		dir, _ := existing.(*dirNode)
		return dir
	}
	dir := f.newDirNode(parent, name, 0755)
	f.attach(parent, dir)
	return dir
}

package memfs

import "os"

func osUID() int { return os.Getuid() }
func osGID() int { return os.Getgid() }

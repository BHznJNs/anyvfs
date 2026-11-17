package main

import (
	"log/slog"
	"os"

	"vfspy/native/internal/bridge"
)

func main() {
	handler := slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{
		Level: slog.LevelDebug,
	})
	slog.SetDefault(slog.New(handler).With("pid", os.Getpid()))

	if err := bridge.Run(); err != nil {
		slog.Error("bridge failed", "error", err)
		os.Exit(1)
	}
}

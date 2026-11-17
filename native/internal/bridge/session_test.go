package bridge

import (
	"context"
	"errors"
	"testing"

	"vfspy/native/internal/queue"
	pb "vfspy/native/internal/rpc"
)

type noticeStream struct {
	pb.Controller_SessionClient
	sent    []*pb.Notice
	sendErr error
	closed  bool
}

func (s *noticeStream) Send(notice *pb.Notice) error {
	if s.sendErr != nil {
		return s.sendErr
	}
	s.sent = append(s.sent, notice)
	return nil
}

func (s *noticeStream) CloseSend() error {
	s.closed = true
	return nil
}

func TestSendNoticesDrainsQueueThroughStopped(t *testing.T) {
	notices := queue.New[*pb.Notice]()
	want := []*pb.Notice{
		{Value: &pb.Notice_Ready{Ready: &pb.Ready{}}},
		{Value: &pb.Notice_Event{Event: &pb.Event{Revision: 1}}},
		{Value: &pb.Notice_Barrier{Barrier: &pb.Barrier{Id: 42}}},
		{Value: &pb.Notice_Stopped{Stopped: &pb.Stopped{}}},
	}
	for _, notice := range want {
		notices.Push(notice)
	}
	afterStopped := &pb.Notice{Value: &pb.Notice_Barrier{Barrier: &pb.Barrier{Id: 43}}}
	notices.Push(afterStopped)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	stream := &noticeStream{}
	if err := sendNotices(ctx, stream, notices, cancel); err != nil {
		t.Fatal(err)
	}
	if !stream.closed || len(stream.sent) != len(want) {
		t.Fatalf("sent %d notices, closed %v", len(stream.sent), stream.closed)
	}
	for i, notice := range stream.sent {
		if notice != want[i] {
			t.Fatalf("notice %d was sent out of order", i)
		}
	}
	if ctx.Err() != nil {
		t.Fatal("clean shutdown canceled the session")
	}
	cancel()
	if got, err := notices.Pop(ctx); err != nil || got != afterStopped {
		t.Fatalf("sender consumed past Stopped: got (%v, %v)", got, err)
	}
}

func TestSendNoticesCancelsSessionOnSendFailure(t *testing.T) {
	notices := queue.New[*pb.Notice]()
	notices.Push(&pb.Notice{Value: &pb.Notice_Event{Event: &pb.Event{Revision: 1}}})
	remaining := &pb.Notice{Value: &pb.Notice_Stopped{Stopped: &pb.Stopped{}}}
	notices.Push(remaining)
	wantErr := errors.New("send failed")
	stream := &noticeStream{sendErr: wantErr}
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	if err := sendNotices(ctx, stream, notices, cancel); !errors.Is(err, wantErr) {
		t.Fatalf("sendNotices: got %v, want %v", err, wantErr)
	}
	if !errors.Is(ctx.Err(), context.Canceled) {
		t.Fatal("send failure did not cancel the session")
	}
	if stream.closed {
		t.Fatal("send failure closed the stream as if shutdown were clean")
	}
	if got, err := notices.Pop(ctx); err != nil || got != remaining {
		t.Fatalf("sender consumed after failure: got (%v, %v)", got, err)
	}
}

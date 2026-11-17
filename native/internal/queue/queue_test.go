package queue

import (
	"context"
	"errors"
	"testing"
	"testing/synctest"
)

func TestQueueFIFOAndReuse(t *testing.T) {
	q := New[*int]()
	a, b := 1, 2
	q.Push(&a)
	q.Push(nil)
	q.Push(&b)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	for _, want := range []*int{&a, nil, &b} {
		got, err := q.Pop(ctx)
		if err != nil || got != want {
			t.Fatalf("Pop: got (%v, %v), want (%v, nil)", got, err, want)
		}
	}
	if got, err := q.Pop(ctx); got != nil || !errors.Is(err, context.Canceled) {
		t.Fatalf("empty Pop: got (%v, %v), want (nil, context.Canceled)", got, err)
	}
	q.Push(&a)
	if got, err := q.Pop(ctx); err != nil || got != &a {
		t.Fatalf("reused Pop: got (%v, %v), want (%v, nil)", got, err, &a)
	}
}

func TestQueueWakesWaitingConsumer(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		q := New[int]()
		done := make(chan struct{})
		go func() {
			defer close(done)
			got, err := q.Pop(t.Context())
			if err != nil || got != 42 {
				t.Errorf("Pop: got (%d, %v), want (42, nil)", got, err)
			}
		}()
		synctest.Wait()
		select {
		case <-done:
			t.Fatal("Pop returned before an item was pushed")
		default:
		}
		q.Push(42)
		<-done
	})
}

func TestQueueCancelsWaitingConsumer(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		q := New[int]()
		ctx, cancel := context.WithCancel(t.Context())
		defer cancel()
		done := make(chan struct{})
		go func() {
			defer close(done)
			got, err := q.Pop(ctx)
			if got != 0 || !errors.Is(err, context.Canceled) {
				t.Errorf("Pop: got (%d, %v), want (0, context.Canceled)", got, err)
			}
		}()
		synctest.Wait()
		select {
		case <-done:
			t.Fatal("Pop returned before cancellation")
		default:
		}
		cancel()
		<-done
	})
}

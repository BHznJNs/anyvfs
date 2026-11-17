// Package queue provides an unbounded FIFO for asynchronous delivery.
package queue

import (
	"context"
	"sync"
)

// Queue is an unbounded FIFO for concurrent producers and a single consumer.
// A Queue must be created with New and must not be copied after first use.
type Queue[T any] struct {
	mu    sync.Mutex
	items []T
	wake  chan struct{}
}

func New[T any]() *Queue[T] {
	return &Queue[T]{wake: make(chan struct{}, 1)}
}

// Push appends an item without waiting for the consumer.
func (q *Queue[T]) Push(item T) {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.items = append(q.items, item)
	select {
	case q.wake <- struct{}{}:
	default:
	}
}

// Pop returns the oldest item, waiting until one is available or ctx is canceled.
// Queued items take precedence over cancellation.
func (q *Queue[T]) Pop(ctx context.Context) (T, error) {
	var zero T
	for {
		q.mu.Lock()
		if len(q.items) > 0 {
			item := q.items[0]
			q.items[0] = zero
			q.items = q.items[1:]
			if len(q.items) == 0 {
				q.items = nil
			}
			q.mu.Unlock()
			return item, nil
		}
		q.mu.Unlock()
		select {
		case <-ctx.Done():
			return zero, ctx.Err()
		case <-q.wake:
		}
	}
}

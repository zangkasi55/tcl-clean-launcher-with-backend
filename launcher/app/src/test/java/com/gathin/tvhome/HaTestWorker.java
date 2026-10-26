package com.gathin.tvhome;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.AbstractExecutorService;
import java.util.concurrent.Callable;
import java.util.concurrent.Delayed;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

final class HaTestWorker extends AbstractExecutorService implements ScheduledExecutorService {
    private final ArrayDeque<Runnable> queue = new ArrayDeque<>();
    private final List<Periodic> periodic = new ArrayList<>();
    private boolean shutdown;

    void drain() {
        int remaining = 1000;
        while (!queue.isEmpty()) {
            if (--remaining == 0) throw new AssertionError("Worker did not settle");
            queue.remove().run();
        }
    }

    void tick() {
        for (Periodic task : new ArrayList<>(periodic)) {
            if (!task.cancelled) queue.add(task.action);
        }
        drain();
    }

    @Override public void execute(Runnable action) {
        if (shutdown) throw new RejectedExecutionException();
        queue.add(action);
    }

    @Override public ScheduledFuture<?> scheduleWithFixedDelay(Runnable action, long initial,
            long delay, TimeUnit unit) {
        Periodic task = new Periodic(action);
        periodic.add(task);
        return task;
    }

    @Override public void shutdown() { shutdown = true; }
    @Override public List<Runnable> shutdownNow() {
        shutdown = true;
        List<Runnable> pending = new ArrayList<>(queue);
        queue.clear();
        return pending;
    }
    @Override public boolean isShutdown() { return shutdown; }
    @Override public boolean isTerminated() { return shutdown && queue.isEmpty(); }
    @Override public boolean awaitTermination(long timeout, TimeUnit unit) { return isTerminated(); }
    @Override public ScheduledFuture<?> schedule(Runnable action, long delay, TimeUnit unit) {
        throw new UnsupportedOperationException();
    }
    @Override public <V> ScheduledFuture<V> schedule(Callable<V> action, long delay, TimeUnit unit) {
        throw new UnsupportedOperationException();
    }
    @Override public ScheduledFuture<?> scheduleAtFixedRate(Runnable action, long initial,
            long period, TimeUnit unit) {
        throw new UnsupportedOperationException();
    }

    private static final class Periodic implements ScheduledFuture<Object> {
        final Runnable action;
        boolean cancelled;
        Periodic(Runnable action) { this.action = action; }
        @Override public boolean cancel(boolean interrupt) { cancelled = true; return true; }
        @Override public boolean isCancelled() { return cancelled; }
        @Override public boolean isDone() { return cancelled; }
        @Override public long getDelay(TimeUnit unit) { return 0; }
        @Override public int compareTo(Delayed value) { return 0; }
        @Override public Object get() { throw new UnsupportedOperationException(); }
        @Override public Object get(long timeout, TimeUnit unit) {
            throw new UnsupportedOperationException();
        }
    }
}

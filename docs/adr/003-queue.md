### 1. Decision

Use **Postgres with `SELECT ... FOR UPDATE SKIP LOCKED`** as the background job queue.

### 2. Why

The current workload is small: **10 documents now, 40 in the gold set, and potentially a few hundred eventually; one user; jobs take seconds to minutes; no throughput or latency requirement**. Introducing Redis or Celery would add another process and another operational dependency without solving a problem this system currently has.

The stronger argument is transactional consistency. The job row and the incident record can be committed in the **same Postgres transaction**, so the system cannot enqueue a job that points to an incident record that was never successfully written. With Redis, the queue and the database become two separate systems, so handling failures between those writes becomes an application responsibility.

Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, allowing multiple workers to claim different jobs without blocking each other. This gives us sufficient concurrency without introducing another service boundary.

### 3. What I rejected

**Redis + RQ.** Redis is a genuinely better queue when job rates are high and provides queue primitives that would otherwise need to be implemented in the application. For this workload, however, it adds a separate service and separates job state from the transactional data that the job operates on.

**Celery.** Celery provides a mature ecosystem for retries, scheduling, chaining, and more complex task workflows. Those capabilities are useful at larger operational scale, but they are unnecessary complexity for a single-user system processing seconds-to-minutes jobs at low volume.

### 4. What I still have to build myself

Choosing Postgres means owning the failure-handling semantics that a dedicated queue would normally provide. The job lifecycle will be:

`queued -> running -> succeeded`

with failure paths:

`running -> queued` for transient failures, and
`running -> dead_letter` for permanent failures or after the retry limit is exhausted.

Transient failures such as **fetch timeouts** are retryable. Permanent failures such as a **404 from the source URL** go directly to `dead_letter` rather than consuming retries.

Workers claim jobs using:

```sql
UPDATE jobs SET status='running', claimed_at=now(), attempts=attempts+1
WHERE id = (
    SELECT id FROM jobs
    WHERE status='queued'
       OR (status='running'
           AND claimed_at < now() - interval '10 minutes')
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *;
```

This single statement performs the **claim**, implements the **visibility timeout**, and acts as the **stuck-job reaper** by allowing another worker to reclaim a job whose claim is older than 10 minutes. No separate reaper process is required.

A worker dying mid-job therefore does not lose the job: once its claim expires, another worker can reclaim it. The queue also needs **retry counting with backoff** and a **dead-letter state after N failed attempts**.

The visibility timeout must exceed the longest expected job duration, but reclaim is still at-least-once rather than exactly-once: a slow live worker can theoretically be reclaimed by another worker, so job completion and side effects must be idempotent.

### 5. When I’d switch

I would move to a dedicated queue when there is a concrete workload or scheduling requirement that Postgres is no longer handling well: for example, **sustained job rates above a few jobs per second, a need for delayed or scheduled jobs, or multiple worker classes requiring different priorities or queue semantics**.

The application should expose a narrow queue interface so the backend implementation is isolated to one module. Switching to Redis/RQ or another queue should therefore not require changes to API handlers, incident processing logic, or worker business logic.

### 6. How I’d know I was wrong

I would revisit the decision if workers spend a measurable portion of their time contending on the claim query, the visibility-timeout reclaim path fires regularly, or queue-wait latency becomes meaningful to the user or the system's throughput.

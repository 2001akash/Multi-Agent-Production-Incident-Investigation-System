from __future__ import annotations

from .data_models import IncidentCase


INCIDENTS: list[IncidentCase] = [
    IncidentCase(
        incident_id="INC-001",
        title="API latency spike",
        report="Orders API latency increased 5x after deployment, error rate rose from 0.1% to 4%.",
        ground_truth="cache warm-up regression in payment service",
        logs=[
            "payment-service: cache miss rate increased to 88% after 11:42 UTC",
            "gateway: p95 latency rising from 200ms to 1100ms",
            "redis: client timeouts observed for 2 minutes during warm-up",
        ],
        metrics=[
            "CPU normal, DB util normal, network steady, cache hit ratio collapsed",
            "HTTP 500s increased on /checkout and /payments endpoints",
        ],
        deployments=[
            "v2.4.9 deployed to payment-service at 11:41 UTC",
            "config: cache warm-up disabled in staging only; prod rollout changed defaults",
        ],
        dependencies=[
            "payment-service depends on redis-cluster and orders-db",
            "redis-cluster latency stayed under 30ms; only cache misses spiked",
        ],
        historical_incidents=[
            "Incident in 2024 showed a warm-up regression after cache defaults were changed",
        ],
        misleading_evidence=[
            "DB replication lag alert briefly fired but later resolved without user impact",
            "External CDN logs show high traffic but checkout traffic was stable",
        ],
        tool_failures={"historical_incidents": "timeout"},
    ),
    IncidentCase(
        incident_id="INC-002",
        title="Customer search outage",
        report="Search results return timeout across all regions after a release candidate was promoted.",
        ground_truth="search index restart loop caused stale shards to be queried",
        logs=[
            "search-index: shard health degraded after restart; 14 shards pending recovery",
            "api-gateway: upstream timeouts on /search with 5-second fallback",
            "search-worker: repeated restart loop at 02:10 UTC",
        ],
        metrics=[
            "search latency p99 > 8s, upstream connection pool exhausted",
            "traffic remained within normal range",
        ],
        deployments=[
            "release rc-2230 promoted to search cluster at 02:05 UTC",
            "config map for shard count increased from 8 to 12 without rollback support",
        ],
        dependencies=[
            "search-api depends on search-index and auth service",
            "auth service remains healthy; only search index degraded",
        ],
        historical_incidents=[
            "Previous search index restart loop caused stale replica reads to saturate network calls",
        ],
        misleading_evidence=[
            "An unrelated auth issue was observed but started 15 minutes earlier and did not affect search",
        ],
        tool_failures={},
    ),
    IncidentCase(
        incident_id="INC-003",
        title="Checkout 429 storms",
        report="Customers are seeing repeated 429s on payment steps after a config change.",
        ground_truth="rate limiting threshold too low in edge gateway",
        logs=[
            "edge-gateway: rate-limit exceeded for checkout user-agent pattern",
            "payment-service: healthy, no retry storm from backend",
            "waf: rate-limit blocks reached but only for checkout traffic",
        ],
        metrics=[
            "429 rate up to 19%, queue depth low, user error rate increased",
            "payment success rate dropped but no backend saturation",
        ],
        deployments=[
            "config change in edge-gateway at 09:12 UTC reduced per-IP rate limit from 120 to 20",
            "no application code change on checkout service",
        ],
        dependencies=[
            "edge gateway sits in front of payment and checkout services",
            "payment provider healthy with normal response time",
        ],
        historical_incidents=[
            "In 2023 a too-low rate limit blocked legitimate customers on mobile checkout",
        ],
        misleading_evidence=[
            "A security alert reported bot traffic, but same bot traffic was excluded in gateway rule",
        ],
        tool_failures={"deployments": "incomplete"},
    ),
    IncidentCase(
        incident_id="INC-004",
        title="Fraud dataset sync delay",
        report="Fraud scoring is stale by 40 minutes; downstream decisions use outdated risk score.",
        ground_truth="Kafka consumer lag from deserialization bug after schema change",
        logs=[
            "fraud-processor: DESERIALIZATION_ERROR after schema update from v6 to v7",
            "kafka-consumer: lag reached 12 million events, offsets not advancing",
        ],
        metrics=[
            "message lag up 400%, consumer backlog rising, risk scores stale for 40 minutes",
            "processor CPU low; no DB bottleneck",
        ],
        deployments=[
            "schema v7 released at 10:20 UTC; consumer restart happened afterwards",
            "no rollback was completed for 40 minutes",
        ],
        dependencies=[
            "fraud-processor consumes kafka-topic-streams and writes to risk-db",
            "risk-db healthy while stream consumer backlog rises",
        ],
        historical_incidents=[
            "Schema drift caused a consumer crash in another pipeline last quarter",
        ],
        misleading_evidence=[
            "Risk-db latency alert fired but it was a false positive after temporary backup job",
        ],
        tool_failures={},
    ),
    IncidentCase(
        incident_id="INC-005",
        title="User login failures after IAM migration",
        report="Users cannot log in after a migration but account creation still works.",
        ground_truth="token issuer mismatch in migrated identity provider route",
        logs=[
            "auth-service: invalid issuer in JWT validation after migration",
            "oidc-proxy: issuer hostname changed from login.example.com to auth.example.com",
        ],
        metrics=[
            "login success rate dropped from 99.8% to 63% in 20 minutes",
            "account creation stable, session refresh failing",
        ],
        deployments=[
            "IAM migration completed 14:00 UTC; OIDC config changed to new issuer URL",
            "web client uses new route but backend still validates old issuer",
        ],
        dependencies=[
            "auth-service depends on oidc-proxy and user-db",
            "user-db healthy and not involved in login failures",
        ],
        historical_incidents=[
            "Six months ago a stale issuer config caused login failures across all clients",
        ],
        misleading_evidence=[
            "A sudden database failover alert persisted, but logins still failed even after DB recovery",
        ],
        tool_failures={"metrics": "incomplete"},
    ),
    IncidentCase(
        incident_id="INC-006",
        title="Inventory mismatch in marketplace",
        report="Inventory counts are inconsistent between web and warehouse after a release.",
        ground_truth="timezone conversion bug in inventory sync job",
        logs=[
            "inventory-sync: update timestamps interpreted in UTC when warehouse sends local time",
            "warehouse reports 3,200 items added; UI still shows 1,200",
        ],
        metrics=[
            "inventory drift increases after 00:30, no effect on payments or search",
            "sync batch success stays high but updates fall behind",
        ],
        deployments=[
            "scheduler updated to UTC conversion at 00:40 UTC; warehouse timezone changed last week",
        ],
        dependencies=[
            "inventory-sync depends on warehouse connector and product catalog service",
            "catalog service healthy; warehouse connector retry logic saturates",
        ],
        historical_incidents=[
            "Mesa-based timezone conversion caused stale inventory in another region",
        ],
        misleading_evidence=[
            "A brief CDN cache purge was mistaken as source, but product detail pages were unaffected",
        ],
        tool_failures={},
    ),
    IncidentCase(
        incident_id="INC-007",
        title="Messaging queue lag",
        report="SMS notifications are delayed for 15 minutes around business hours.",
        ground_truth="bulk export job starved queue workers by consuming CPU",
        logs=[
            "sms-worker: queue depth rising steadily; worker CPU hit 99%",
            "batch-export: 12 long-running exports running concurrently",
        ],
        metrics=[
            "queue lag increased from 30s to 920s, worker saturation explains delay",
            "delivery success remains okay outside the export window",
        ],
        deployments=[
            "batch-export scheduled every hour at 09:00 but the cron job was not throttled",
        ],
        dependencies=[
            "sms-worker depends on queue and notification-db",
            "queue consumers are the single limiting factor; database is healthy",
        ],
        historical_incidents=[
            "Previous export job starved workers during peak traffic and was fixed by cap concurrency",
        ],
        misleading_evidence=[
            "SMS provider latency rose but recovery occurred before queue backlog recovered",
        ],
        tool_failures={"logs": "timeout"},
    ),
    IncidentCase(
        incident_id="INC-008",
        title="Analytics dashboard stale data",
        report="Analytics dashboard shows stale daily totals for the last 2 hours even though sources are healthy.",
        ground_truth="aggregator job timezone shift caused missing partition window",
        logs=[
            "analytics-agg: scheduled run skipped partitions for UTC-5 window",
            "pipeline: last successful run at 01:45; current run at 03:00 has no new data",
        ],
        metrics=[
            "source ingestion healthy, but dashboard data shows no update since 00:00",
            "aggregator success count stable but partition windows are empty",
        ],
        deployments=[
            "job cron shifted by 1 hour after DST config change in analytics cluster",
        ],
        dependencies=[
            "dashboard reads from analytics-agg output and source warehouse",
            "source warehouse healthy and event ingestion continues",
        ],
        historical_incidents=[
            "Another dashboard had stale totals after timezone conversion in a cron job",
        ],
        misleading_evidence=[
            "A display cache invalidation bug was suspected, but API responses from aggregator were stale too",
        ],
        tool_failures={},
    ),
    IncidentCase(
        incident_id="INC-009",
        title="Refund errors after feature flag rollout",
        report="Refund requests fail intermittently after a new rollout, with only some customer cohorts affected.",
        ground_truth="feature flag targeting used the wrong customer segment key",
        logs=[
            "refund-service: feature flag evaluation returned false for allowed cohort; other cohorts unaffected",
            "event-stream: customer segment key mismatch in targeting config",
        ],
        metrics=[
            "refund success dropped 8% for specific cohort only; overall gateway metrics healthy",
            "error logs show target cohort missing in allowlist",
        ],
        deployments=[
            "feature flag rollout at 12:15 UTC changed segment key from user_id to account_id",
            "only some customers impacted because their account IDs were not mapped",
        ],
        dependencies=[
            "refund-service depends on feature-flag-service and payment-gateway",
            "payment-gateway healthy; issue is isolated to feature flag evaluation",
        ],
        historical_incidents=[
            "Flag targeting bug was previously traced to the wrong segment dimension",
        ],
        misleading_evidence=[
            "Gateway retries were elevated but only for the affected cohort; no evidence of provider outages",
        ],
        tool_failures={"historical_incidents": "timeout"},
    ),
    IncidentCase(
        incident_id="INC-010",
        title="Order processing slowdown",
        report="Order throughput slowed gradually after a release, but no full outages are observed.",
        ground_truth="query planner regression created poor index use on order status table",
        logs=[
            "orders-db: plan cache invalidated frequently after upgrade; query time out observed for status table",
            "orders-api: p95 latency increased from 450ms to 1430ms over 30 minutes",
        ],
        metrics=[
            "DB CPU normal, storage not saturated, but slow queries on status table spiked",
            "throughput dropped 28% with no increased error rate",
        ],
        deployments=[
            "database engine tuned release at 07:45 UTC; plan cache behavior changed after upgrade",
        ],
        dependencies=[
            "orders-api depends on orders-db and inventory-api",
            "inventory-api healthy; slowdown isolated to DB query plan",
        ],
        historical_incidents=[
            "A planner regression on a status table caused a 20% throughput drop with low CPU utilization",
        ],
        misleading_evidence=[
            "Autoscale events were triggered but were a downstream effect, not the root cause",
        ],
        tool_failures={"metrics": "timeout"},
    ),
]

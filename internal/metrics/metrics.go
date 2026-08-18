package metrics

import "github.com/prometheus/client_golang/prometheus"

var (
	AcquireDuration = prometheus.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "sandbox_acquire_seconds",
		Help:    "Seconds spent acquiring a ready sandbox.",
		Buckets: []float64{0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10},
	}, []string{"source"})

	PoolSize = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Name: "sandbox_pool_size",
		Help: "Current managed sandbox count by state.",
	}, []string{"state"})

	ClaimConflicts = prometheus.NewCounter(prometheus.CounterOpts{
		Name: "sandbox_claim_conflicts_total",
		Help: "Number of optimistic CAS claim conflicts.",
	})

	ExecDuration = prometheus.NewHistogram(prometheus.HistogramOpts{
		Name:    "sandbox_exec_seconds",
		Help:    "Seconds spent executing commands in sandboxes.",
		Buckets: prometheus.DefBuckets,
	})

	ExecTimeouts = prometheus.NewCounter(prometheus.CounterOpts{
		Name: "sandbox_exec_timeouts_total",
		Help: "Number of sandbox commands terminated by context timeout or cancellation.",
	})

	PlanDenied = prometheus.NewCounterVec(prometheus.CounterOpts{
		Name: "sandbox_plan_denied_total",
		Help: "Number of rejected write plans by bounded reason.",
	}, []string{"reason"})

	RuntimeInfo = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Name: "sandbox_runtime_info",
		Help: "Configured sandbox runtime class.",
	}, []string{"runtime"})
)

func init() {
	prometheus.MustRegister(
		AcquireDuration,
		PoolSize,
		ClaimConflicts,
		ExecDuration,
		ExecTimeouts,
		PlanDenied,
		RuntimeInfo,
	)
}

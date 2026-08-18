package approval

import (
	"context"
	"errors"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes/fake"
	clienttesting "k8s.io/client-go/testing"
)

func TestProposeDryRunThenApprove(t *testing.T) {
	client := fake.NewClientset(testDeployment())
	dryRunUpdates := 0
	client.Fake.PrependReactor("update", "deployments", func(action clienttesting.Action) (bool, runtime.Object, error) {
		update := action.(interface {
			clienttesting.UpdateAction
			GetUpdateOptions() metav1.UpdateOptions
		})
		if len(update.GetUpdateOptions().DryRun) > 0 {
			dryRunUpdates++
			// fake client 不理解 DryRun；拦截后返回副本，确保 tracker 没被修改。
			return true, update.GetObject().DeepCopyObject(), nil
		}
		return false, nil, nil
	})

	service := NewService(client)
	plan, err := service.Propose(context.Background(), ProposeInput{
		Namespace: "sandboxd-target",
		Name:      "approval-demo",
		Replicas:  1,
	})
	if err != nil {
		t.Fatalf("Propose: %v", err)
	}
	if !plan.DryRunValidated || dryRunUpdates != 1 {
		t.Fatalf("DryRunValidated=%v dryRunUpdates=%d", plan.DryRunValidated, dryRunUpdates)
	}
	before, _ := client.AppsV1().Deployments("sandboxd-target").Get(context.Background(), "approval-demo", metav1.GetOptions{})
	if *before.Spec.Replicas != 0 {
		t.Fatalf("dry-run 不应落地，replicas=%d", *before.Spec.Replicas)
	}

	approved, err := service.Approve(context.Background(), plan.ID)
	if err != nil {
		t.Fatalf("Approve: %v", err)
	}
	if approved.Status != StatusApproved {
		t.Fatalf("status=%s", approved.Status)
	}
	after, _ := client.AppsV1().Deployments("sandboxd-target").Get(context.Background(), "approval-demo", metav1.GetOptions{})
	if *after.Spec.Replicas != 1 {
		t.Fatalf("批准后 replicas=%d", *after.Spec.Replicas)
	}
}

func TestApproveRejectsChangedTarget(t *testing.T) {
	client := fake.NewClientset(testDeployment())
	client.Fake.PrependReactor("update", "deployments", func(action clienttesting.Action) (bool, runtime.Object, error) {
		update := action.(interface {
			clienttesting.UpdateAction
			GetUpdateOptions() metav1.UpdateOptions
		})
		if len(update.GetUpdateOptions().DryRun) > 0 {
			return true, update.GetObject().DeepCopyObject(), nil
		}
		return false, nil, nil
	})

	service := NewService(client)
	plan, err := service.Propose(context.Background(), ProposeInput{
		Namespace: "sandboxd-target",
		Name:      "approval-demo",
		Replicas:  1,
	})
	if err != nil {
		t.Fatalf("Propose: %v", err)
	}

	changed := testDeployment()
	changed.ResourceVersion = "8"
	if err := client.Tracker().Update(
		appsv1.SchemeGroupVersion.WithResource("deployments"),
		changed,
		"sandboxd-target",
	); err != nil {
		t.Fatalf("修改 tracker: %v", err)
	}

	stale, err := service.Approve(context.Background(), plan.ID)
	if !errors.Is(err, ErrTargetChanged) {
		t.Fatalf("Approve error=%v", err)
	}
	if stale.Status != StatusStale {
		t.Fatalf("status=%s", stale.Status)
	}
	current, _ := client.AppsV1().Deployments("sandboxd-target").Get(context.Background(), "approval-demo", metav1.GetOptions{})
	if *current.Spec.Replicas != 0 {
		t.Fatalf("stale Plan 不应执行，replicas=%d", *current.Spec.Replicas)
	}
}

func testDeployment() *appsv1.Deployment {
	replicas := int32(0)
	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:            "approval-demo",
			Namespace:       "sandboxd-target",
			UID:             types.UID("uid-1"),
			ResourceVersion: "7",
		},
		Spec: appsv1.DeploymentSpec{Replicas: &replicas},
	}
}

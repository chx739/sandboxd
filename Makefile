GO ?= go

.PHONY: build test demo

build:
	$(GO) build ./...

test:
	$(GO) test ./...

demo:
	./hack/demo.sh

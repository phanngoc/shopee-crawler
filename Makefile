build:
	CGO_ENABLED=1 go build -o shopee-crawler .

run:
	./shopee-crawler --display :99

run-headless:
	./shopee-crawler --display ""

reset:
	./shopee-crawler --reset --display :99

clean:
	rm -f shopee-crawler shopee.db

.PHONY: build run run-headless reset clean

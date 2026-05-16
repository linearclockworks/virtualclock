# virtualclock — common workflows
# Usage: make prep SKU=LCK-1051
#        make calibrate SKU=LCK-1051
#        make build SKU=LCK-1051

SKU ?= LCK-XXXX
FACE_IMG ?= $(SKU)-front.png
PTR_IMG  ?= $(SKU)-pointer.png

# Prep: split product photo into face + pointer PNGs
prep:
	python3 separate_pointer.py $(FACE_IMG) --pointer $(PTR_IMG)

# Calibrate: open browser UI, click Done → generates final HTML automatically
calibrate:
	python3 build_clock.py $(SKU) --calibrate

# Build: generate customer HTML with current/default cal values
build:
	python3 build_clock.py $(SKU)

# Build with manual cal overrides
build-cal:
	python3 build_clock.py $(SKU) --left $(LEFT) --right $(RIGHT) --track $(TRACK)

.PHONY: prep calibrate build build-cal

#!/bin/sh
# Copyright (C) 2006-2019 OpenWrt.org
# MH7021: the status light is an RGB triplet (red/green/blue:status), so the two
# maintenance states use magenta (red+blue) -- failsafe fast, sysupgrade slow --
# which none of the network states shown by /usr/sbin/neon-led use. Everything
# else is the stock file.

. /lib/functions/leds.sh

boot="$(get_dt_led boot)"
failsafe="$(get_dt_led failsafe)"
running="$(get_dt_led running)"
upgrade="$(get_dt_led upgrade)"
[ -e /sys/class/leds/red:status ] && [ -e /sys/class/leds/blue:status ] && rgb=1

rgb_magenta() {	# rgb_magenta <delay_on> <delay_off> [hold_s]  (hold: keep neon-led from repainting)
	led_off green:status
	led_timer red:status "$1" "$2"
	led_timer blue:status "$1" "$2"
	[ -n "$3" ] && { read up _ < /proc/uptime; echo "magenta fast $(( ${up%.*} + $3 ))" > /var/run/neon-led.override; }
}

set_led_state() {
	status_led="$boot"

	case "$1" in
	preinit)
		status_led_blink_preinit
		;;
	failsafe)
		[ -n "$rgb" ] && { rgb_magenta 50 50 15; return; }
		status_led_off
		[ -n "$running" ] && {
			status_led="$running"
			status_led_off
		}
		status_led="$failsafe"
		status_led_blink_failsafe
		;;
	preinit_regular)
		status_led_blink_preinit_regular
		;;
	upgrade)
		[ -n "$rgb" ] && { rgb_magenta 200 200; return; }
		[ -n "$running" ] && {
			status_led="$running"
			status_led_off
		}
		status_led="$upgrade"
		status_led_blink_preinit_regular
		;;
	done)
		status_led_off
		[ "$status_led" != "$running" ] && \
			status_led_restore_trigger "boot"
		[ -n "$running" ] && {
			status_led="$running"
			status_led_on
		}
		;;
	esac
}

set_state() {
	[ -n "$boot" -o -n "$failsafe" -o -n "$running" -o -n "$upgrade" ] && set_led_state "$1"
}

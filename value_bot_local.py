
    try:
        scan_once(state)
    except Exception:
        log.exception("Błąd wstępnego skanu")

    while True:
        started = time.monotonic()
        try:
            scan_once(state)
        except Exception:
            log.exception("Błąd cyklu skanowania")
        elapsed = time.monotonic() - started
        time.sleep(max(5, SCAN_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    main()

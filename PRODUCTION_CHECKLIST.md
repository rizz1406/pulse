# Pulse production checklist

Code-controlled readiness is covered by automated tests, `/healthz`, login throttling,
secure session cookies on Render, full JSON backup/restore, nutrition confidence labels,
label scanning, and exact barcode logging.

External account actions still require the owner:

1. Upgrade the Render service from `free` to an always-on paid plan if cold starts are unacceptable.
2. Point an uptime monitor at `https://pulse-v24w.onrender.com/healthz` and alert on non-200 responses.
3. Enable the preferred Turso backup/retention option, and periodically download a full backup from Analytics.
4. Verify install, camera, barcode, label scanning, goal editing, and backup download on the actual Android/iPhone devices used.
5. Monitor Gemini, Groq, FatSecret, and OpenFoodFacts quotas/errors in Render logs.

Never store API keys, tokens, passcodes, or downloaded user backups in Git.

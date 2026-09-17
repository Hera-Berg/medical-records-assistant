"""The desktop app: a launcher around the server, not a replacement for it.

It starts the same server ``health-agent serve`` starts, on ``127.0.0.1`` and
nowhere else, opens the person's own browser at it, and puts an icon in the menu
bar or system tray with four things in it: Open, what the reader is doing, Open
the folder, and Quit. Quitting stops the server, and the server stopping stops
the reader.

**Not a webview.** The consultation summary is a printable A4 sheet, and it is
this project's whole output. Real browsers print and print to PDF reliably;
embedded webviews do not. So the app opens the browser the person already uses,
and the recorder's secure-context requirement is met the same way it always was
— by the address being ``127.0.0.1``.

**The first run needs no terminal.** With no record folder yet, the server
starts in a setup mode that asks where the folder should live and then which
computer reads documents; everything else lives in Settings.

The CLI is unchanged and complete. ``health-agent app`` runs this launcher from
a pip install too, and the frozen build runs exactly the same function.
"""

# Health Record {version}

A health record you keep yourself: an ordinary folder on your own computer, with
your documents in it and plain text files describing them. This app reads what
you add, asks you before anything important goes in, and prints a one-page
summary to hand to a doctor.

## Which file to download

| Your computer | Download |
|---|---|
| Mac with Apple Silicon (M1 or later) | {mac_arm64} |
| Windows (64-bit Intel or AMD) | {windows_x64} |

{not_built}

## This app is not signed

Apple and Microsoft sell certificates that let an app say who made it, and they
cost money every year. This project has not bought one. So your computer will
warn you before it opens the app the first time, and again for each new version.
What to press is below.

Not being signed does not change what the app does. What you can check:

- **The source code is public**, in this repository.
- **Every download has a checksum** in `SHA256SUMS.txt` beside it. If your
  file's checksum matches, your copy is exactly the one published here.
  On a Mac: `shasum -a 256 <file>`. On Windows, in PowerShell:
  `Get-FileHash <file>`.
- **Every download has a build record** saying which workflow run, and which
  commit of this repository, produced it. With the GitHub command-line tool:
  `gh attestation verify <file> --repo {repository}`. That record says where the
  file came from. It is not a signature, nobody is vouching for the file by
  issuing it, and your computer will still warn you exactly as described below.

None of these can tell you that what was published is safe. Only reading the
source, or trusting people who have, can.

## Opening it on a Mac

1. Open the `.dmg` and drag **Health Record** into **Applications**.
2. Open it from Applications. macOS says it cannot check the app for malicious
   software. Press **Done** (on older versions, **Cancel**).
3. Open **System Settings**, then **Privacy & Security**. Near the bottom it
   says Health Record was blocked. Press **Open Anyway**, then confirm.

On macOS 14 (Sonoma) and earlier you can instead right-click the app in
Applications, choose **Open**, and then **Open** again.

Health Record lives in the menu bar at the top of the screen, not in the Dock.
It opens your web browser; that is where the record is.

## Opening it on Windows

1. Your browser may say the file is not commonly downloaded. Choose **Keep**
   (in Edge: the **…** menu, then **Keep**, then **Keep anyway**).
2. Run the installer. Windows says it protected your PC. Press **More info**,
   then **Run anyway**.
3. It installs for you only and needs no administrator password.

Health Record lives in the system tray at the bottom right of the screen —
Windows hides new icons under the **^** arrow. Opening it again from the Start
menu while it is running takes you straight back to your record.

## The first time it opens

It asks two things in your browser: where to keep your record (a folder on this
computer is the recommended answer) and which computer reads your documents.
Reading on this computer needs a one-time download of about {reader_download}
— the app says exactly how much and asks before it starts. Nothing from your
record is sent anywhere.

## What was checked for this release

Each check ran on the finished download, on a GitHub-hosted machine, in the
workflow run that built it.

{results}

{unverified}

## Where things are kept, and removing it

- **Your record**: the folder you chose. Never touched by uninstalling.
- **This computer's settings for the app**: `.config/health-agent` in your home
  folder.
- **The reading program and model**, about {reader_download}: on a Mac,
  `Library/Application Support/health-agent` in your home folder; on Windows,
  `AppData\Local\health-agent`. Delete that folder to get the space back.

To remove the app: on a Mac, drag it from Applications to the Bin; on Windows,
uninstall **Health Record** from Settings, then Apps.

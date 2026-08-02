Attendance Management System v1.0.0 - Release Folder
======================================================

The documentation in this folder (CHANGELOG.md and every PDF) was
generated in the Linux development environment and is ready to ship
as-is.

Setup.exe and Portable.exe are Windows PE binaries and cannot be produced
on Linux -- they are not present in this folder yet. Get them one of two
ways:

  1. Automated (recommended, no Windows machine needed): push an
     "attendance-v1.0.0"-style tag to the repository (the "attendance-"
     prefix matters -- this repository hosts more than one project, and
     an unprefixed "v1.0.0" tag already belongs to a different one), or
     trigger ".github/workflows/windows-release.yml" manually from the
     Actions tab. It builds both files on a GitHub-hosted Windows runner
     and attaches them to the matching GitHub Release (or its own
     workflow Artifacts, for a manual run) within a few minutes.

  2. Manual, on a real Windows machine:

         cd attendance_system
         packaging\build_all.bat

     which writes Setup.exe and Portable.exe directly into this folder
     alongside the documentation already here.

See BUILD_WINDOWS.md at the project root for the full procedure,
prerequisites, and how to verify the installer on a clean Windows
machine.

Final contents after either build path has run:
  Setup.exe                     - full installer (built on Windows/CI)
  Portable.exe                  - standalone executable (built on Windows/CI)
  README.pdf                    - project overview
  Release Notes.pdf              - what's new in this version
  Installation Guide.pdf         - end-customer install/upgrade/uninstall steps
  User Manual.pdf                - day-to-day usage guide
  Administrator Manual.pdf       - licensing/admin/backup/troubleshooting guide
  CHANGELOG.md                   - full technical version history

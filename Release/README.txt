Attendance Management System - Release Folder
================================================

The documentation in this folder (README.pdf, User Manual.pdf,
Administrator Manual.pdf, CHANGELOG.md) was generated in the Linux
development environment and is ready to ship as-is.

Setup.exe and Portable.exe are Windows PE binaries and cannot be produced
on Linux -- they are not present in this folder yet. Build them on a
Windows machine with:

    cd attendance_system
    packaging\build_all.bat

which writes Setup.exe and Portable.exe directly into this folder
alongside the documentation already here. See BUILD_WINDOWS.md at the
project root for the full procedure, prerequisites, and how to verify the
installer on a clean Windows machine.

Final contents after a Windows build has run:
  Setup.exe                     - full installer (built on Windows)
  Portable.exe                  - standalone executable (built on Windows)
  README.pdf                    - project overview
  User Manual.pdf                - end-user guide
  Administrator Manual.pdf       - installation/admin/licensing guide
  CHANGELOG.md                   - version history

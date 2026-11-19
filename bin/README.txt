Put these three files here before running build.bat:

  ffmpeg.exe
  ffprobe.exe
  node.exe

Find your current ones with:   where ffmpeg    and    where node

Careful: if "where" shows a tiny shim (Scoop, Chocolatey, WinGet links), copying it
will NOT work on another PC. The real ffmpeg.exe is 100+ MB-ish, node.exe is ~80 MB.
Download real copies if needed:
  ffmpeg + ffprobe: https://www.gyan.dev/ffmpeg/builds/  (release essentials, take bin\*.exe)
  node:             https://nodejs.org/en/download       (Windows Binary .zip, take node.exe)

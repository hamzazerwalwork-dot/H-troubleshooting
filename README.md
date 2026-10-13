install apple devices 
install MSYM 2
https://www.msys2.org/
plug the device
verify it by clicking trust
open MSYM 2
pacman -Syu
pacman -S mingw-w64-ucrt-x86_64-libimobiledevice mingw-w64-ucrt-x86_64-python
set PATH=%PATH%;C:\msys64\ucrt64\bin

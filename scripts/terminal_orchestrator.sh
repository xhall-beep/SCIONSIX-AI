#!/data/data/com.termux/files/usr/bin/bash
# SCIONSIX TERMINAL AGENT CORE - ANDROID 16 FIX
export SCIONSIX_HOME=$HOME/scionsix

while true; do
  echo -n "SCIONSIX> " >&2
  read -r command
  
  case "$command" in
    exit) break ;;
    update)
      cd "$SCIONSIX_HOME" || exit
      # Initialize Git if first run
      [ ! -d .git ] && git init && git commit -m "Initial" --allow-empty
      ./gradlew assembleDebug --no-daemon || {
        echo "Build failed! Installing Android build tools..."
        pkg install -y android-tools aapt ndk-sysroot
        ./gradlew assembleDebug --no-daemon
      }
      termux-open app/build/outputs/apk/debug/app-debug.apk 2>/dev/null || {
        echo "APK ready at: $SCIONSIX_HOME/app/build/outputs/apk/debug/app-debug.apk"
        echo "MANUAL INSTALL: Open in file manager and tap to install"
      }
      ;;
    exec*)
      # Android 16 non-root execution path
      echo "${command#exec }" | /data/data/com.termux/files/usr/bin/sh
      ;;
    *)
      echo "Available: update, exec [cmd], exit" >&2 
      ;;
  esac
done

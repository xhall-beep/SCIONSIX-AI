#!/bin/sh
##############################################################################
# Gradle start up script for UN*X (minimal wrapper launcher)
##############################################################################

# Resolve the script's directory, following symlinks.
app_path=$0
while [ -h "$app_path" ]; do
  ls=$(ls -ld "$app_path")
  link=${ls#*' -> '}
  case $link in
    /*) app_path=$link ;;
    *)  app_path=${app_path%/*}/$link ;;
  esac
done
APP_HOME=$(cd "${app_path%/*}" >/dev/null 2>&1 && pwd -P)

if [ -n "$JAVA_HOME" ]; then
  JAVACMD="$JAVA_HOME/bin/java"
else
  JAVACMD="java"
fi

# JVM options first, then classpath, then the wrapper main class, then Gradle args.
# shellcheck disable=SC2086
exec "$JAVACMD" \
  -Xmx64m -Xms64m \
  $JAVA_OPTS $GRADLE_OPTS \
  "-Dorg.gradle.appname=gradlew" \
  -classpath "$APP_HOME/gradle/wrapper/gradle-wrapper.jar" \
  org.gradle.wrapper.GradleWrapperMain "$@"

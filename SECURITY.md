# SCIONSIX AI SECURITY PROTOCOL (Android 16)

## Critical Requirements
1. Enable in Android Settings:
   - Settings > Security > Special app access > Install unknown apps > **ENABLE FOR TERMUX**
   - Settings > Apps > Termux > Permissions > **ALL PERMISSIONS ENABLED**

2. Termux-specific hardening:
```bash
   termux-setup-storage
   tsu -c 'setenforce 0'  # Required for Android 16 terminal access
```

3. First-run command:
```bash
   cd ~/scionsix && ./scripts/terminal_orchestrator.sh
```

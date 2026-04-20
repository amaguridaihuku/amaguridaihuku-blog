#!/bin/bash
# ============================================================
# 甘栗大福 管理画面ランチャーアプリ作成スクリプト
# 実行方法: bash admin/create-launcher.sh
# ============================================================

BLOG_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Desktop/甘栗大福管理画面.app"

echo "📦 ランチャーアプリを作成中..."

# ── アプリ構造 ─────────────────────────────────────────────
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"

# ── 実行スクリプト ─────────────────────────────────────────
cat > "$APP/Contents/MacOS/start" << SCRIPT
#!/bin/bash
# すでに起動中か確認
if lsof -Pi :8888 -sTCP:LISTEN -t >/dev/null 2>&1; then
  open http://localhost:8888
else
  cd "$BLOG_DIR"
  python3 admin/server.py > /tmp/amaguridaihuku-admin.log 2>&1 &
  # サーバー起動を待つ
  for i in \$(seq 1 10); do
    sleep 0.5
    lsof -Pi :8888 -sTCP:LISTEN -t >/dev/null 2>&1 && break
  done
  open http://localhost:8888
fi
SCRIPT
chmod +x "$APP/Contents/MacOS/start"

# ── Info.plist ─────────────────────────────────────────────
cat > "$APP/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>start</string>
  <key>CFBundleName</key>
  <string>甘栗大福管理画面</string>
  <key>CFBundleDisplayName</key>
  <string>甘栗大福管理画面</string>
  <key>CFBundleIdentifier</key>
  <string>com.amaguridaihuku.admin</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleVersion</key>
  <string>1.0</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

echo ""
echo "✅ デスクトップに「甘栗大福管理画面」アプリを作成しました！"
echo ""
echo "🚀 使い方:"
echo "   デスクトップの「甘栗大福管理画面」をダブルクリック"
echo "   → 自動的にブラウザが開きます"
echo ""
echo "💡 初回だけ: 右クリック →「開く」→「開く」ボタンを押してください"
echo "   (Appleのセキュリティ確認をスキップ)"

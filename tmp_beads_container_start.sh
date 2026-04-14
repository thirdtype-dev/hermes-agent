#!/usr/bin/env bash
set -euo pipefail
npm i -g --ignore-scripts @beads/bd@0.61.0 beads-ui@0.12.0 >/tmp/beads-install.log 2>&1
node <<'NODE'
const https = require('https');
const fs = require('fs');
const url = 'https://github.com/steveyegge/beads/releases/download/v0.61.0/beads_0.61.0_linux_amd64.tar.gz';
function dl(u) {
  https.get(u, res => {
    if ([301, 302, 303, 307, 308].includes(res.statusCode)) {
      dl(res.headers.location);
      return;
    }
    if (res.statusCode !== 200) {
      console.error('HTTP', res.statusCode);
      process.exit(1);
    }
    const f = fs.createWriteStream('/tmp/bd.tgz');
    res.pipe(f);
    f.on('finish', () => f.close(() => process.exit(0)));
  }).on('error', e => {
    console.error(e);
    process.exit(1);
  });
}
dl(url);
NODE
tar -xzf /tmp/bd.tgz -C /usr/local/bin bd
chmod +x /usr/local/bin/bd
export BD_BIN=/usr/local/bin/bd
export HOME=/data
export BEADS_DIR=/data/.beads
cd /data
exec bdui start --host 0.0.0.0 --port 3000

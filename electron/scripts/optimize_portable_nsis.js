const fs = require('fs');
const path = require('path');

const portableNsiPath = path.resolve(__dirname, '..', '..', 'node_modules', 'app-builder-lib', 'templates', 'nsis', 'portable.nsi');

function optimizePortableNsi() {
  if (!fs.existsSync(portableNsiPath)) {
    return;
  }

  let content = fs.readFileSync(portableNsiPath, 'utf8');

  // If content contains old optimization, unwrap it cleanly to raw extraction
  const oldOptRegex = /\$\{If\}\s+\$\{FileExists\}[\s\S]*?; Portable workspace is already cached[\s\S]*?\$\{Else\}\r?\n([\s\S]*?)\r?\n\s*\$\{EndIf\}\r?\n\r?\n\s*/;
  if (oldOptRegex.test(content)) {
    content = content.replace(oldOptRegex, (m, p1) => {
      return p1.replace(/FileOpen \$R9[\s\S]*?FileClose \$R9\r?\n\s*/g, '').trim() + '\r\n\r\n  ';
    });
  }

  const extractionStart = 'RMDir /r $INSTDIR\n  SetOutPath $INSTDIR';
  const extractionStartCrLf = 'RMDir /r $INSTDIR\r\n  SetOutPath $INSTDIR';

  let hasCrLf = content.includes('\r\n');
  let startMarker = hasCrLf ? extractionStartCrLf : extractionStart;

  if (!content.includes(startMarker)) {
    const alt1 = '  RMDir /r $INSTDIR\r\n    SetOutPath $INSTDIR';
    const alt2 = '    RMDir /r $INSTDIR\r\n    SetOutPath $INSTDIR';
    const alt3 = '  RMDir /r $INSTDIR\n    SetOutPath $INSTDIR';
    if (content.includes(alt1)) startMarker = alt1;
    else if (content.includes(alt2)) startMarker = alt2;
    else if (content.includes(alt3)) startMarker = alt3;
    else return;
  }

  const envVarMarker = "System::Call 'Kernel32::SetEnvironmentVariable(t, t)i (\"PORTABLE_EXECUTABLE_DIR\"";
  const envIndex = content.indexOf(envVarMarker);
  if (envIndex === -1) return;

  const startIndex = content.indexOf(startMarker);
  const extractionBlock = content.substring(startIndex, envIndex);

  const crlf = hasCrLf ? '\r\n' : '\n';
  const optimizedBlock = 
    `\${If} \${FileExists} "$PLUGINSDIR\\app-64.7z"${crlf}` +
    `  FileOpen $R9 "$INSTDIR\\package.json" r${crlf}` +
    `  \${If} $R9 != ""${crlf}` +
    `    FileClose $R9${crlf}` +
    `    ; Fast startup: Portable workspace already extracted${crlf}` +
    `  \${Else}${crlf}` +
    `    ${extractionBlock.trim()}${crlf}` +
    `  \${EndIf}${crlf}` +
    `\${Else}${crlf}` +
    `  ${extractionBlock.trim()}${crlf}` +
    `\${EndIf}${crlf}${crlf}  `;

  content = content.substring(0, startIndex) + optimizedBlock + content.substring(envIndex);
  fs.writeFileSync(portableNsiPath, content, 'utf8');
}

optimizePortableNsi();

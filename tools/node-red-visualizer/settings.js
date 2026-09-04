/**
 * PC-local development monitor. The editor is deliberately loopback-only:
 * access it at http://127.0.0.1:1880 after starting the local process.
 */
module.exports = {
  uiPort: 1880,
  uiHost: "127.0.0.1",
  flowFile: "flows.json",
  editorTheme: {
    projects: { enabled: false }
  }
};

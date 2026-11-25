// Açılış ekranı ile ana süreç arasında sınırlı, güvenli köprü
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("kurgu", {
  onStatus: (cb) => ipcRenderer.on("status", (_e, data) => cb(data)),
  appInfo: () => ipcRenderer.invoke("app-info"),
});

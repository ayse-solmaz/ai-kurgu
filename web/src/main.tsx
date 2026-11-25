import "@astryxdesign/theme-neutral/fonts.css";
import "@astryxdesign/core/reset.css";
import "@astryxdesign/core/astryx.css";
import "@astryxdesign/theme-neutral/theme.css";
import "./index.css";

import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { Center } from "@astryxdesign/core/Center";
import { Theme } from "@astryxdesign/core/theme";
import { neutralTheme } from "@astryxdesign/theme-neutral/built";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

/** Bir bileşen çökerse tüm sayfa boşalmasın; hatayı gösterip yeniden denemeye izin ver. */
class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <Center height="100%">
        <Banner
          status="error"
          title="Arayüzde bir hata oluştu"
          description={this.state.error.message}
          endContent={<Button label="Yeniden dene" variant="primary" onClick={() => this.setState({ error: null })} />}
        />
      </Center>
    );
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Theme theme={neutralTheme} mode="dark">
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </Theme>
  </React.StrictMode>,
);

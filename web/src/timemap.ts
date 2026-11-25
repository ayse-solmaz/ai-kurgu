// Kaynak zaman <-> çıktı zamanı (backend'deki TimeMap'in aynısı)

export class TimeMap {
  spans: [number, number, number][] = [];
  duration = 0;

  constructor(keep: [number, number][]) {
    let t = 0;
    for (const [a, b] of keep) {
      this.spans.push([a, b, t]);
      t += b - a;
    }
    this.duration = t;
  }

  /** Kaynak zaman tutulan bir parçadaysa çıktı zamanı, değilse null. */
  toOut(t: number): number | null {
    for (const [a, b, o] of this.spans) if (t >= a - 1e-3 && t <= b + 1e-3) return o + Math.min(Math.max(t - a, 0), b - a);
    return null;
  }

  /** Kesik bölgedeyse bir sonraki tutulan parçanın başı. */
  nextKeep(t: number): number | null {
    for (const [a, b] of this.spans) {
      if (t < a) return a;
      if (t <= b) return t;
    }
    return null;
  }

  toSrc(out: number): number {
    for (const [a, b, o] of this.spans) if (out <= o + (b - a) + 1e-6) return a + Math.max(0, out - o);
    const last = this.spans[this.spans.length - 1];
    return last ? last[1] : 0;
  }

  /** Çıktıda kesim noktalarının konumları. */
  cutPoints(): number[] {
    return this.spans.slice(1).map((s) => s[2]);
  }
}

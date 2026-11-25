from pathlib import Path

import pytest

from kurgu.core.config import load_preset
from kurgu.core.timeline import Anchor, BRoll, SourceInfo, Timeline, Zoom
from kurgu.generate.base import AssetResult, ProviderUnavailable
from kurgu.generate.router import AssetRouter
from kurgu.render.render import cut_lines, zoom_expr


class FakeProject:
    def __init__(self, root: Path):
        self.dir = root
        self.assets_dir = root / "assets"
        self.assets_dir.mkdir(parents=True, exist_ok=True)

    def transcript(self):
        raise FileNotFoundError

    def rel(self, p):
        return Path(p).resolve().relative_to(self.dir.resolve()).as_posix()

    def abs(self, rel):
        return (self.dir / rel).resolve()


class Broken:
    name = "broken"

    def check(self):
        pass

    def generate(self, req, out_dir):
        raise RuntimeError("GPU yok")


class Works:
    name = "works"
    calls = 0

    def check(self):
        pass

    def generate(self, req, out_dir):
        Works.calls += 1
        p = out_dir / f"clip_{req.cache_key(self.name)}.mp4"
        p.write_bytes(b"x")
        return AssetResult(p, self.name, "image", "atıf")


class Unavailable:
    name = "off"

    def check(self):
        raise ProviderUnavailable("anahtar yok")


def make_tl():
    src = SourceInfo(path="x.mp4", duration=30, width=1920, height=1080, fps=30)
    tl = Timeline.from_preset(load_preset("shorts"), src)
    tl.brolls = [BRoll(id="b1", anchor=Anchor(start=5, end=8), prompt="galaxy")]
    return tl


def test_router_falls_back_and_caches(tmp_path, monkeypatch):
    import kurgu.generate.router as router

    providers = {"off": Unavailable, "broken": Broken, "works": Works}
    monkeypatch.setattr(router, "make_provider", lambda n: providers[n]())
    proj = FakeProject(tmp_path)
    r = AssetRouter(proj, ["off", "broken", "works"])
    assert r.status["off"] == "anahtar yok" and r.status["works"] == "hazır"

    tl = make_tl()
    rep = r.fill(tl)
    assert rep.filled == ["b1"] and tl.brolls[0].provider == "works" and tl.brolls[0].attribution == "atıf"

    # ikinci çalıştırmada önbellekten gelir, üretici tekrar çağrılmaz
    tl2 = make_tl()
    before = Works.calls
    AssetRouter(proj, ["off", "broken", "works"]).fill(tl2)
    assert Works.calls == before and tl2.brolls[0].asset == tl.brolls[0].asset


def test_router_reports_when_nothing_available(tmp_path, monkeypatch):
    import kurgu.generate.router as router

    monkeypatch.setattr(router, "make_provider", lambda n: Unavailable())
    rep = AssetRouter(FakeProject(tmp_path), ["off"]).fill(make_tl())
    assert "b1" in rep.failed


def test_cut_graph_and_zoom_expression():
    lines, out = cut_lines([(0.0, 2.0), (5.0, 7.5)], "0:a", "a")
    assert out == "acat" and "concat=n=2:v=0:a=1" in lines[-1]
    assert "afade=t=out:st=2.4880" in lines[1]  # 2.5 sn parça, 12 ms geçiş
    single, _ = cut_lines([(1.0, 3.0)], "0:v", "v")
    assert single[-1] == "[v0]null[vcat]"

    tl = make_tl()
    tl.zooms = [Zoom(id="z1", anchor=Anchor(start=10, end=12), scale=1.2)]
    expr = zoom_expr(tl, None, tl.time_map())
    assert expr.startswith("1+0.200*clip((it-10.000)")

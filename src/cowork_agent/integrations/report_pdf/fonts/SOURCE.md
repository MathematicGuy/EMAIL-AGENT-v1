# Bundled Noto Sans assets

The source variable fonts and license were downloaded from the official
[`google/fonts`](https://github.com/google/fonts) repository at commit
`6a003b5eb672dc8bf5bff5937cf5863f8b175445`:

- `ofl/notosans/NotoSans[wdth,wght].ttf`
- `ofl/notosans/NotoSans-Italic[wdth,wght].ttf`
- `ofl/notosans/OFL.txt`

The four bundled static instances were produced from those sources with
`fonttools varLib.instancer`, fixing `wdth=100` and `wght=400` or `wght=700`.
This moves variation work out of the request path. The font files are
distributed under the SIL Open Font License in `OFL.txt`.

Recorded SHA-256 digests of the bundled assets:

```text
89e2a9bc43a162aad29ea0c1cd7226dd711bfc27fb0885093adacdb9174c2b2d  NotoSans-Regular.ttf
61d516489ce6ee508f8a22d1f5c0da039011f7e5d42b9e6587f58c386e8769d4  NotoSans-Bold.ttf
e3ebb049ad618474167da21f2a84bfde8ce05235973228d9a01c1228aad84a87  NotoSans-Italic.ttf
5e75cfad5753f9e74585dfe009961603eccbaaa0e23ce6744df1ab513f37e4e9  NotoSans-BoldItalic.ttf
e2e177a32561584d4fc13aaa3cd8e53758a12910f013fe9ca125419111722029  OFL.txt
```

The bundled license differs from the upstream bytes only by removal of one
trailing space so repository whitespace checks remain clean.

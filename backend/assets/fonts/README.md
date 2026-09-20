# Bundled font assets

Inter 4.1, static regular and bold TTFs from the [official release](https://github.com/rsms/inter/releases/tag/v4.1).
Source archive: `Inter-4.1.zip`, entries `extras/ttf/Inter-Regular.ttf` and `extras/ttf/Inter-Bold.ttf`.
The accompanying `LICENSE.txt` is the SIL Open Font License 1.1.

Font bytes are included in the visual configuration hash. Replacing them creates a new visual revision;
an earlier approval does not cover different font bytes. Pillow uses its BASIC layout engine to avoid
host-dependent optional shaping libraries for the initial Latin-script template.

# rutatravels-guides

Curated city guides for the Ruta Travels app, served to the app from
`https://guides.rutatravels.com`.

Each file is `<city>-<days>day-<lang>.json`. Nine languages: en, es, it, fr, de, ja, ar, zh, pt.

`validate-guides.py` runs on every push and blocks a malformed guide from reaching the CDN.

See NOTICE.md for rights. The build tooling lives in the private application repository.

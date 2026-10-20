# Shopify Radar

Yeni açılan Shopify mağazalarını keşfeden, ürünlerini toplayan ve mağazalar arası trendleri izleyen, **tamamen ücretsiz** çalışan bir araştırma sistemi.

## Ne yapar?

1. **Mağaza keşfi:** Günlük yeni alan adı listesini tarar. DNS ile Shopify adaylarını eler, `/products.json` ile doğrular. `.tr` alan adları öncelikli; ayrıca crt.sh sertifika kayıtlarından yeni `.tr` alan adları toplanır ve Türkçe görünen `.com` adları öne alınır.
2. **Ürün toplama:** Mağazaların herkese açık `/products.json` verisini ve `TRY` para birimini kaydeder. Para birimi TRY olmasa bile sayfa dili/ürün adları Türkçe ise mağaza Türk sayılır.
3. **Sıralama takibi:** Herkese açık "çok satanlar" sıralamasını her taramada kaydeder. Bu **gerçek satış adedi değildir**, sadece sıralama değişimidir.
4. **Mağazalar arası eşleştirme:** Ürün görsellerinin pHash parmak izini karşılaştırarak farklı isimlerle satılan aynı ürünü bulur. Görseller saklanmaz, yalnızca hash kaydedilir.
5. **Rapor:** `docs/data.json` ve `docs/index.html` üretir (GitHub Pages veya Cloudflare Pages ile ücretsiz yayınlanır).

## Kurulum (hepsi ücretsiz)

1. GitHub'da yeni bir depo açın (public olursa Actions dakikaları sınırsızdır) ve bu klasörün içeriğini yükleyin.
2. Depo → **Settings → Actions → General → Workflow permissions → Read and write permissions** seçin.
3. **Actions** sekmesinden `scan` iş akışını elle çalıştırın (`Run workflow`). Sonrasında günde 2 kez otomatik çalışır.
4. Web arayüzü için: **Settings → Pages → Branch: main, Folder: /docs**. Adres `kullanici.github.io/depo-adi` olur.

## Yerelde çalıştırma

```bash
pip install -r requirements.txt
python -m pytest                         # testler
python -m radar.cli run --file domains.txt   # kendi domain listenizle
python -m radar.cli run                  # WhoisDS günlük listesiyle
```

Komutlar ayrı da çalışır: `discover`, `collect`, `report`.

## Önemli notlar

- **Domain kaynağı:** WhoisDS'in ücretsiz listesi zaman zaman değişebilir veya indirme başarısız olabilir. Bu durumda `domains.txt` ile kendi listenizi verin (ör. Certstream/crt.sh çıktısı).
- **Nazik tarama:** İstekler yavaş ve sınırlıdır, kimlik bildiren bir User-Agent kullanılır. Sınırları artırmadan önce mağazaların kullanım şartlarını düşünün.
- **Kişisel veri toplamayın.** Mağaza sahibi iletişim bilgilerini toplayıp toplu mesaj atmak KVKK açısından risklidir.
- **Veritabanı** (`data/radar.db`) repoda tutulur ve her taramada commit edilir. Çok büyürse Supabase ücretsiz katmanına taşınabilir.
- Üyelik sistemi bu sürümde yok. Sonraki adım olarak Supabase Auth ile eklenebilir.

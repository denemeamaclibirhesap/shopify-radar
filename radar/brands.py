"""Bilinen marka tespiti: yeni/bağımsız mağazaları köklü markalardan ayırmak için.

İki kaynak birleşir:
  * Aşağıdaki elle hazırlanmış liste (alan adının asıl kısmı birebir eşleşmelidir;
    "nike-outlet.shop" gibi taklit/bayi adlar eşleşmez).
  * Majestic Million'da yer alan köklü siteler (tablo: established).
Listeyi büyütmek için KNOWN_BRANDS'e küçük harfle, tire ve boşluksuz ad eklemek yeterli.
"""
from . import sources

KNOWN_BRANDS = set("""
nike adidas reebok puma newbalance asics fila converse vans underarmour skechers hoka salomon
columbia thenorthface timberland lacoste hilfiger tommyhilfiger calvinklein ralphlauren levis wrangler
diesel guess armani emporioarmani versace gucci prada dior chanel hermes burberry fendi balenciaga
zara bershka pullandbear stradivarius massimodutti hm cosstores uniqlo
decathlon intersport sportsdirect jdsports footlocker
samsung sony lg philips bosch siemens arcelik beko vestel dyson xiaomi huawei oppo lenovo hp dell asus acer msi
logitech razer canon nikon gopro jbl bose
ikea lego playmobil barbie mattel hasbro
sephora maybelline loreal nivea garnier rimmel nyx clinique esteelauder lancome
amazon hepsiburada trendyol n11 ciceksepeti gittigidiyor
koton lcwaikiki defacto mavi colins ltbjeans lufian vakko beymen boyner
dagi derimod reeder jumbo flyingtiger dreametech ipekyol machka morhipo
yargici kigili damatandtweed hatemoglu avva ramsey pierrecardin tanca
flo instreet greyder hotic desa sarar
koctas bauhaus tekzen englishhome madamecoco karaca emsan korkmaz pasabahce
chakra gratis watsons rossmann farmasi flormar goldenrose
migros carrefoursa bim a101 macrocenter
teknosa mediamarkt vatan incehesap itopya
turkcell vodafone turktelekom
""".split())


def brand_core(domain: str) -> str:
    """Alan adının asıl adı: shop.nike.com -> nike, nike.com.tr -> nike, x.co.uk -> x."""
    d = domain.lower().strip(".")
    if d.endswith(".tr"):
        core = sources.registrable_tr(d).split(".")[0]
    else:
        parts = d.split(".")
        if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "org", "net", "gov", "ac"}:
            core = parts[-3]
        elif len(parts) >= 2:
            core = parts[-2]
        else:
            core = parts[0]
    return core.replace("-", "")


def is_known_brand(domain: str, established: frozenset | set = frozenset()) -> bool:
    return domain in established or brand_core(domain) in KNOWN_BRANDS

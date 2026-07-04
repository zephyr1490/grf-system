# -*- coding: utf-8 -*-
"""
Statische Klasse -> Fahrzeug-Zuordnung (WRC-Autos pro Fahrzeugklasse).
Quelle: vehicles_by_class.csv (Owner-gepflegt, nicht von RaceNet API).

ACHTUNG (Owner-Hinweis): manche Fahrzeuge tauchen in mehreren Klassen auf
(z.B. Volkswagen Polo GTI R5 in Rally2 UND WRC2, Skoda Fabia RS Rally2 in
Rally2 UND WRC3). Das ist so beabsichtigt/bekannt. Nicht 100%% verifiziert,
ob RaceNet dafuer exakt denselben Namensstring verwendet - betrifft aber
nur moderne Klassen (Rally2/Rally3/Rally4/WRC/WRC2-4), die aktuell nicht
genutzt werden. Vorerst bewusst nicht deduplifiziert.
"""

VEHICLE_CLASSES = {
    "F2 Kit Car": [
        {
            "name": "Citroën Xsara Kit Car",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Ford Escort Mk 6 Maxi",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Peugeot 306 Maxi",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Renault Maxi Mégane",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "SEAT Ibiza Kit Car",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Vauxhall Astra Rally Car",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Volkswagen Golf IV Kit Car",
            "drivetrain": "FWD",
            "era": "Historic"
        }
    ],
    "Group A": [
        {
            "name": "Ford Escort RS Cosworth",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "Lancia Delta HF Integrale",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "Mitsubishi Galant VR4",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "SUBARU Impreza 1995",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "SUBARU Legacy RS",
            "drivetrain": "AWD",
            "era": "Historic"
        }
    ],
    "Group B (4WD)": [
        {
            "name": "Audi Sport quattro S1 (E2)",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "Ford RS200",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "Lancia Delta S4",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "MG Metro 6R4",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "Peugeot 205 T16 Evo 2",
            "drivetrain": "AWD",
            "era": "Historic"
        }
    ],
    "Group B (RWD)": [
        {
            "name": "BMW M1 Procar Rally",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Lancia 037 Evo 2",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Opel Manta 400",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Porsche 911 SC RS",
            "drivetrain": "RWD",
            "era": "Historic"
        }
    ],
    "H1 (FWD)": [
        {
            "name": "Lancia Fulvia HF",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "MINI Cooper S",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Vauxhall Nova Sport",
            "drivetrain": "FWD",
            "era": "Historic"
        }
    ],
    "H2 (FWD)": [
        {
            "name": "Peugeot 205 GTI",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Peugeot 309 GTI",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Volkswagen Golf GTI",
            "drivetrain": "FWD",
            "era": "Historic"
        }
    ],
    "H2 (RWD)": [
        {
            "name": "Alpine Renault A110 1600 S",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Fiat 131 Abarth Rally",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Ford Escort MK2",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Ford Escort RS 1600 MK1",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Hillman Avenger",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Opel Kadett C GT/E",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Talbot Sunbeam Lotus",
            "drivetrain": "RWD",
            "era": "Historic"
        }
    ],
    "H3 (RWD)": [
        {
            "name": "BMW M3 Evo Rally",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Ford Escort MK2 McRae Motorsport",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Ford Sierra Cosworth RS500",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Lancia Stratos",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Opel Ascona 400",
            "drivetrain": "RWD",
            "era": "Historic"
        },
        {
            "name": "Renault 5 Turbo",
            "drivetrain": "RWD",
            "era": "Historic"
        }
    ],
    "JWRC": [
        {
            "name": "Ford Fiesta Rally3",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Ford Fiesta Rally3 Evo",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Junior WRC Builder Vehicle",
            "drivetrain": "AWD",
            "era": "Modern"
        }
    ],
    "NR4/R4": [
        {
            "name": "McRae R4",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Mitsubishi Lancer Evolution X",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "SUBARU WRX STI NR4",
            "drivetrain": "AWD",
            "era": "Modern"
        }
    ],
    "Rally2": [
        {
            "name": "Ford Fiesta R5 MK7 Evo 2",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Peugeot 208 T16 R5",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Volkswagen Polo GTI R5",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "ŠKODA Fabia RS Rally2",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "ŠKODA Fabia Rally2 Evo",
            "drivetrain": "AWD",
            "era": "Modern"
        }
    ],
    "Rally3": [
        {
            "name": "Renault Clio Rally3",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Ford Fiesta Rally3",
            "drivetrain": "AWD",
            "era": "Modern"
        }
    ],
    "Rally4": [
        {
            "name": "Citroën C2 R2 Max",
            "drivetrain": "FWD",
            "era": "Modern"
        },
        {
            "name": "Ford Fiesta MK8 Rally4",
            "drivetrain": "FWD",
            "era": "Modern"
        },
        {
            "name": "Opel Adam R2",
            "drivetrain": "FWD",
            "era": "Modern"
        },
        {
            "name": "Opel Corsa Rally4",
            "drivetrain": "FWD",
            "era": "Modern"
        },
        {
            "name": "Peugeot 208 Rally4",
            "drivetrain": "FWD",
            "era": "Modern"
        },
        {
            "name": "Renault Clio Rally4",
            "drivetrain": "FWD",
            "era": "Modern"
        },
        {
            "name": "Renault Twingo II",
            "drivetrain": "FWD",
            "era": "Modern"
        }
    ],
    "S1600": [
        {
            "name": "Citroën C2 Super 1600",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Citroën Saxo Super 1600",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Ford Puma S1600",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Peugeot 206 S1600",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Renault Clio S1600",
            "drivetrain": "FWD",
            "era": "Historic"
        }
    ],
    "S2000": [
        {
            "name": "Fiat Grande Punto Abarth S2000",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Opel Corsa S2000",
            "drivetrain": "FWD",
            "era": "Historic"
        },
        {
            "name": "Peugeot 207 S2000",
            "drivetrain": "FWD",
            "era": "Historic"
        }
    ],
    "WRC": [
        {
            "name": "Ford Puma Rally1 HYBRID (2023)",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Ford Puma Rally1 HYBRID (2024)",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Hyundai i20 N Rally1 HYBRID (2023)",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Hyundai i20 N Rally1 HYBRID (2024)",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Toyota GR Yaris Rally1 HYBRID (2023)",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Toyota GR Yaris Rally1 HYBRID (2024)",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "WRC Builder Vehicle",
            "drivetrain": "AWD",
            "era": "Modern"
        }
    ],
    "WRC2": [
        {
            "name": "Citroën C3 Rally2",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Ford Fiesta Rally2",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Hyundai i20 N Rally2",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Toyota GR Yaris Rally2",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Volkswagen Polo GTI R5",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "WRC2 Builder Vehicle",
            "drivetrain": "AWD",
            "era": "Modern"
        }
    ],
    "WRC3": [
        {
            "name": "ŠKODA Fabia RS Rally2",
            "drivetrain": "AWD",
            "era": "Modern"
        }
    ],
    "WRC4": [
        {
            "name": "ŠKODA Fabia Rally2 Evo",
            "drivetrain": "AWD",
            "era": "Modern"
        }
    ],
    "World Rally Car 1997 - 2011": [
        {
            "name": "Citroën C4 WRC",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "Citroën Xsara WRC",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "Ford Focus RS Rally 2001",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "Ford Focus RS Rally 2008",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "Ford Focus WRC '99",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "MINI Countryman Rally Edition",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "Mitsubishi Lancer Evolution VI",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "Peugeot 206 Rally",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "SEAT Córdoba WRC",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "SUBARU Impreza 1998",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "SUBARU Impreza 2001",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "SUBARU Impreza 2008",
            "drivetrain": "AWD",
            "era": "Historic"
        },
        {
            "name": "ŠKODA Fabia WRC",
            "drivetrain": "AWD",
            "era": "Historic"
        }
    ],
    "World Rally Car 2012 - 2016": [
        {
            "name": "Citroën DS3 WRC '12",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "MINI John Cooper Works WRC",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Volkswagen Polo R WRC 2013",
            "drivetrain": "AWD",
            "era": "Modern"
        }
    ],
    "World Rally Car 2017 - 2021": [
        {
            "name": "Citroën C3 WRC",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Ford Fiesta WRC",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Hyundai i20 Coupe WRC '21",
            "drivetrain": "AWD",
            "era": "Modern"
        },
        {
            "name": "Volkswagen Polo 2017",
            "drivetrain": "AWD",
            "era": "Modern"
        }
    ]
}

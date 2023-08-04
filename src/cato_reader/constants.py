DRUGS_OF_INTEREST = [
    'Doxorubicinhydrochlorid',
    'Dacarbazin',
    'Cisplatin',
    '5-Fluorouracil',
    'Cardioxane',
    'Irinotecanhydrochlorid-Trihydrat',
    'Ribosofol INJ/INF-LOE',
    'Oxaliplatin',
    'Oncofolic',
    'Cetuximab',
    'Calciumfolinat', # Kabi, Gry
    'Uromitexan Multidose',
    'Ifosfamid',
    'Bevacizumab',
    'Unacid',
]

DRUGS_OF_NOTE = [
    'Dexamethason',
    'Granisetron',
]

DRUGS_OF_LOW_PRIORITY = {
    'NaCl 0,9% 250ml Flasche Glas SWB': 'NaCl'
}

DRUG_COMBINATIONS = {
    'Dexa+Granisetron': ('Dexamethason', 'Granisetron'),
}


EXCLUDED_TREATMENT_KEYWORDS = [
    'alternativ',
    'parallel',
    'spülen',
    'nach',
    'vor',
    'hinweis',
    'beachten'
]


# Sanity check that we don't accidentally exclude a drug from the start
for kw in EXCLUDED_TREATMENT_KEYWORDS:
    if any([kw in drug for drug in DRUGS_OF_INTEREST]):
        raise ValueError('Excluded keyword in list of drugs of interest!')

    if any([kw in drug for drug in DRUGS_OF_INTEREST]):
        raise ValueError('Excluded keyword in list of drugs of note!')



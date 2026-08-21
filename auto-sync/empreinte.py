#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Empreinte d'identifiant d'evenement Meta, cote serveur.

Doit rendre EXACTEMENT la meme valeur que la fonction sdgEmpreinte() des pages
d'optin. C'est toute la raison d'etre de ce fichier : sans identifiant commun,
un envoi par l'API Conversions ferait doublon avec le pixel du navigateur au
lieu de le completer.

La correspondance est verifiee par test_empreinte.py, qui EXTRAIT le JS du
fragment livre et compare les deux implementations sur un jeu de chaines.

Usage :
    from empreinte import eid_lead
    eid_lead("Jean.Dupont@Gmail.com ", "2026-08-02")   -> 'lead-xxxxxxxxxxxxxxxx'
"""

M32 = 0xFFFFFFFF


def empreinte(txt: str) -> str:
    """Deux FNV-1a de graines differentes, concatenes en 16 caracteres hexa.

    Non cryptographique, et ca n'a pas a l'etre : on cherche un identifiant
    reproductible, pas un secret. Les operations reproduisent Math.imul et le
    decalage non signe de JavaScript, d'ou les masques sur 32 bits.
    """
    a = 0x811C9DC5
    b = 0x01000193
    for ch in txt:
        c = ord(ch)
        a = (((a ^ c) & M32) * 0x01000193) & M32
        b = (((((b + c) & M32) ^ (b >> 13)) & M32) * 0x85EBCA6B) & M32
    return f"{a:08x}{b:08x}"


def eid_lead(email: str, date_live: str) -> str:
    """Identifiant de l'evenement Lead pour un inscrit.

    date_live au format AAAA-MM-JJ : elle entre dans l'empreinte pour qu'un
    habitue inscrit a plusieurs lives ne porte pas le meme identifiant partout,
    ce qui ferait fusionner des conversions distinctes cote Meta.
    """
    return "lead-" + empreinte(email.strip().lower() + "|" + date_live)


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3:
        print(eid_lead(sys.argv[1], sys.argv[2]))
    else:
        print("usage: python3 empreinte.py <email> <AAAA-MM-JJ>")

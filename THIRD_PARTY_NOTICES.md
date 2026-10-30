# FolderUndo dependency notices

Authored source/data retain the existing MIT LICENSE. App imports use stdlib/project-local modules. These four packages are test prerequisites, not vendored runtime code. All seven declared License-File payloads, including pytest AUTHORS, follow in full. No license alternative was selected. Byte ranges in the manifest preserve exact source text.

Observed unbundled prerequisites (not version locks): CPython3.12.3/Linux x86-64; pip24.0 for test installation (installed metadata declares MIT); Node26.7.0 for optional UI tests; Node Playwright1.58.2/nested playwright-core1.58.2 (Apache-2.0 declared in accessreplay/node_modules/playwright/package.json); browser API153.0.8010.12. Playwright LICENSE/NOTICE remain external. The Python browser_journey helper needs separately supplied Python Playwright and its hard-coded browser, outside requirements.lock. Complete Node/browser license inventory is not asserted. FFmpeg/libflite are media-production tools; producer binaries/voice models are not shipped here. No whole-host redistribution obligation is inferred.

Existing pip report wheel hashes match the lock, but it lacks invocation/time/exit/revision/target-venv binding: it does not prove executed --require-hashes/current clean installation. At base revision 90b14b5, prior no-pip delivery matches nine app modules and the lock; README/static assets differ. G-ENVIRONMENT current setup/API/CLI evidence remains separate. This inventory records observed dependency metadata. Installation and release verification are separate.

## iniconfig 2.3.1 — LICENSE

The MIT License (MIT)

Copyright (c) 2010 - 2023 Holger Krekel and others

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## packaging 26.3 — LICENSE

This software is made available under the terms of *either* of the licenses
found in LICENSE.APACHE or LICENSE.BSD. Contributions to this software is made
under the terms of *both* these licenses.

## packaging 26.3 — LICENSE.APACHE


                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS

## packaging 26.3 — LICENSE.BSD

Copyright (c) Donald Stufft and individual contributors.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
       this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
       notice, this list of conditions and the following disclaimer in the
       documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## pluggy 1.7.0 — LICENSE

The MIT License (MIT)

Copyright (c) 2015 holger krekel (rather uses bitbucket/hpk42)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## pytest 8.3.5 — LICENSE

The MIT License (MIT)

Copyright (c) 2004 Holger Krekel and others

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## pytest 8.3.5 — AUTHORS

Holger Krekel, holger at merlinux eu
merlinux GmbH, Germany, office at merlinux eu

Contributors include::

Aaron Coleman
Abdeali JK
Abdelrahman Elbehery
Abhijeet Kasurde
Adam Johnson
Adam Stewart
Adam Uhlir
Ahn Ki-Wook
Akhilesh Ramakrishnan
Akiomi Kamakura
Alan Velasco
Alessio Izzo
Alex Jones
Alex Lambson
Alexander Johnson
Alexander King
Alexei Kozlenok
Alice Purcell
Allan Feldman
Aly Sivji
Amir Elkess
Anatoly Bubenkoff
Anders Hovmöller
Andras Mitzki
Andras Tim
Andrea Cimatoribus
Andreas Motl
Andreas Zeidler
Andrew Shapton
Andrey Paramonov
Andrzej Klajnert
Andrzej Ostrowski
Andy Freeland
Anita Hammer
Anthon van der Neut
Anthony Shaw
Anthony Sottile
Anton Grinevich
Anton Lodder
Antony Lee
Arel Cordero
Arias Emmanuel
Ariel Pillemer
Armin Rigo
Aron Coyle
Aron Curzon
Arthur Richard
Ashish Kurmi
Aviral Verma
Aviv Palivoda
Babak Keyvani
Barney Gale
Ben Brown
Ben Gartner
Ben Leith
Ben Webb
Benjamin Peterson
Benjamin Schubert
Bernard Pratz
Bo Wu
Bob Ippolito
Brian Dorsey
Brian Larsen
Brian Maissy
Brian Okken
Brianna Laugher
Bruno Oliveira
Cal Jacobson
Cal Leeming
Carl Friedrich Bolz
Carlos Jenkins
Ceridwen
Charles Cloud
Charles Machalow
Charnjit SiNGH (CCSJ)
Cheuk Ting Ho
Chris Mahoney
Chris Lamb
Chris NeJame
Chris Rose
Chris Wheeler
Christian Boelsen
Christian Clauss
Christian Fetzer
Christian Neumüller
Christian Theunert
Christian Tismer
Christine Mecklenborg
Christoph Buelter
Christopher Dignam
Christopher Gilling
Claire Cecil
Claudio Madotto
Clément M.T. Robert
Cornelius Riemenschneider
CrazyMerlyn
Cristian Vera
Cyrus Maden
Damian Skrzypczak
Daniel Grana
Daniel Hahler
Daniel Miller
Daniel Nuri
Daniel Sánchez Castelló
Daniel Valenzuela Zenteno
Daniel Wandschneider
Daniele Procida
Danielle Jenkins
Daniil Galiev
Dave Hunt
David Díaz-Barquero
David Mohr
David Paul Röthlisberger
David Peled
David Szotten
David Vierra
Daw-Ran Liou
Debi Mishra
Denis Kirisov
Denivy Braiam Rück
Dheeraj C K
Dhiren Serai
Diego Russo
Dmitry Dygalo
Dmitry Pribysh
Dominic Mortlock
Duncan Betts
Edison Gustavo Muenz
Edoardo Batini
Edson Tadeu M. Manoel
Eduardo Schettino
Edward Haigh
Eero Vaher
Eli Boyarski
Elizaveta Shashkova
Éloi Rivard
Endre Galaczi
Eric Hunsberger
Eric Liu
Eric Siegerman
Eric Yuan
Erik Aronesty
Erik Hasse
Erik M. Bray
Evan Kepner
Evgeny Seliverstov
Fabian Sturm
Fabien Zarifian
Fabio Zadrozny
Farbod Ahmadian
faph
Felix Hofstätter
Felix Nieuwenhuizen
Feng Ma
Florian Bruhin
Florian Dahlitz
Floris Bruynooghe
Frank Hoffmann
Fraser Stark
Gabriel Landau
Gabriel Reis
Garvit Shubham
Gene Wood
George Kussumoto
Georgy Dyuldin
Gergely Kalmár
Gleb Nikonorov
Graeme Smecher
Graham Horler
Greg Price
Gregory Lee
Grig Gheorghiu
Grigorii Eremeev (budulianin)
Guido Wesdorp
Guoqiang Zhang
Harald Armin Massa
Harshna
Henk-Jaap Wagenaar
Holger Kohr
Hugo van Kemenade
Hui Wang (coldnight)
Ian Bicking
Ian Lesperance
Ilya Konstantinov
Ionuț Turturică
Isaac Virshup
Israel Fruchter
Itxaso Aizpurua
Iwan Briquemont
Jaap Broekhuizen
Jake VanderPlas
Jakob van Santen
Jakub Mitoraj
James Bourbeau
James Frost
Jan Balster
Janne Vanhala
Jason R. Coombs
Javier Domingo Cansino
Javier Romero
Jeff Rackauckas
Jeff Widman
Jenni Rinker
Jens Tröger
John Eddie Ayson
John Litborn
John Towler
Jon Parise
Jon Sonesen
Jonas Obrist
Jordan Guymon
Jordan Moldow
Jordan Speicher
Joseph Hunkeler
Joseph Sawaya
Josh Karpel
Joshua Bronson
Jurko Gospodnetić
Justice Ndou
Justyna Janczyszyn
Kale Kundert
Kamran Ahmad
Kenny Y
Karl O. Pinc
Karthikeyan Singaravelan
Katarzyna Jachim
Katarzyna Król
Katerina Koukiou
Keri Volans
Kevin C
Kevin Cox
Kevin Hierro Carrasco
Kevin J. Foley
Kian Eliasi
Kian-Meng Ang
Kodi B. Arfer
Kojo Idrissa
Kostis Anagnostopoulos
Kristoffer Nordström
Kyle Altendorf
Lawrence Mitchell
Lee Kamentsky
Leonardus Chen
Lev Maximov
Levon Saldamli
Lewis Cowles
Llandy Riveron Del Risco
Loic Esteve
lovetheguitar
Lukas Bednar
Luke Murphy
Maciek Fijalkowski
Maggie Chung
Maho
Maik Figura
Mandeep Bhutani
Manuel Krebber
Marc Mueller
Marc Schlaich
Marcelo Duarte Trevisani
Marcin Bachry
Marc Bresson
Marco Gorelli
Mark Abramowitz
Mark Dickinson
Mark Vong
Marko Pacak
Markus Unterwaditzer
Martijn Faassen
Martin Altmayer
Martin K. Scherer
Martin Prusse
Mathieu Clabaut
Matt Bachmann
Matt Duck
Matt Williams
Matthias Hafner
Maxim Filipenko
Maximilian Cosmo Sitter
mbyt
Michael Aquilina
Michael Birtwell
Michael Droettboom
Michael Goerz
Michael Krebs
Michael Seifert
Michael Vogt
Michal Wajszczuk
Michał Górny
Michał Zięba
Mickey Pashov
Mihai Capotă
Mihail Milushev
Mike Hoyle (hoylemd)
Mike Lundy
Milan Lesnek
Miro Hrončok
mrbean-bremen
Nathan Goldbaum
Nathaniel Compton
Nathaniel Waisbrot
Nauman Ahmed
Ned Batchelder
Neil Martin
Neven Mundar
Nicholas Devenish
Nicholas Murphy
Niclas Olofsson
Nicolas Delaby
Nicolas Simonds
Nico Vidal
Nikolay Kondratyev
Nipunn Koorapati
Oleg Pidsadnyi
Oleg Sushchenko
Olga Matoula
Oliver Bestwalter
Omar Kohl
Omer Hadari
Ondřej Súkup
Oscar Benjamin
Parth Patel
Patrick Hayes
Patrick Lannigan
Paul Müller
Paul Reece
Pauli Virtanen
Pavel Karateev
Paweł Adamczak
Pedro Algarvio
Petter Strandmark
Philipp Loose
Pierre Sassoulas
Pieter Mulder
Piotr Banaszkiewicz
Piotr Helm
Poulami Sau
Prakhar Gurunani
Prashant Anand
Prashant Sharma
Pulkit Goyal
Punyashloka Biswal
Quentin Pradet
q0w
Ralf Schmitt
Ralph Giles
Ram Rachum
Ran Benita
Raphael Castaneda
Raphael Pierzina
Rafal Semik
Reza Mousavi
Raquel Alegre
Ravi Chandra
Reagan Lee
Rob Arrow
Robert Holt
Roberto Aldera
Roberto Polli
Roland Puntaier
Romain Dorgueil
Roman Bolshakov
Ronny Pfannschmidt
Ross Lawley
Ruaridh Williamson
Russel Winder
Russell Martin
Ryan Puddephatt
Ryan Wooden
Sadra Barikbin
Saiprasad Kale
Samuel Colvin
Samuel Dion-Girardeau
Samuel Jirovec
Samuel Searles-Bryant
Samuel Therrien (Avasam)
Samuele Pedroni
Sanket Duthade
Sankt Petersbug
Saravanan Padmanaban
Sean Malloy
Segev Finer
Serhii Mozghovyi
Seth Junot
Shantanu Jain
Sharad Nair
Shubham Adep
Simon Blanchard
Simon Gomizelj
Simon Holesch
Simon Kerr
Skylar Downes
Srinivas Reddy Thatiparthy
Stefaan Lippens
Stefan Farmbauer
Stefan Scherfke
Stefan Zimmermann
Stefanie Molin
Stefano Taschini
Steffen Allner
Stephan Obermann
Sven-Hendrik Haase
Sviatoslav Sydorenko
Sylvain Marié
Tadek Teleżyński
Takafumi Arakaki
Taneli Hukkinen
Tanvi Mehta
Tanya Agarwal
Tarcisio Fischer
Tareq Alayan
Tatiana Ovary
Ted Xiao
Terje Runde
Thomas Grainger
Thomas Hisch
Tianyu Dongfang
Tim Hoffmann
Tim Strazny
TJ Bruno
Tobias Diez
Tom Dalton
Tom Viner
Tomáš Gavenčiak
Tomer Keren
Tony Narlock
Tor Colvin
Trevor Bekolay
Tushar Sadhwani
Tyler Goodlet
Tyler Smart
Tzu-ping Chung
Vasily Kuznetsov
Victor Maryama
Victor Rodriguez
Victor Uriarte
Vidar T. Fauske
Vijay Arora
Virendra Patil
Virgil Dupras
Vitaly Lashmanov
Vivaan Verma
Vlad Dragos
Vlad Radziuk
Vladyslav Rachek
Volodymyr Kochetkov
Volodymyr Piskun
Wei Lin
Wil Cooley
William Lee
Wim Glenn
Wouter van Ackooy
Xixi Zhao
Xuan Luong
Xuecong Liao
Yannick Péroux
Yao Xiao
Yoav Caspi
Yuliang Shao
Yusuke Kadowaki
Yutian Li
Yuval Shimon
Zac Hatfield-Dodds
Zach Snicker
Zachary Kneupper
Zachary OBrien
Zhouxin Qiu
Zoltán Máté
Zsolt Cserna

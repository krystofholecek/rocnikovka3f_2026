// Malá vestavěná ukázková data (vlastní text, styl dat kolem roku 2005: e-maily, blogy, diskuze).
// Každá položka = jeden dokument (v pipeline se mezi ně vkládá <|endoftext|>).

export const SAMPLE_DOCS = [
  `Hi Kenneth, the meeting about the California energy contracts is moved to Friday at 10 am. Please bring the latest numbers for the trading desk and the notes from the call with the legal team. Thanks, Sara`,

  `Re: server outage on Tuesday

The mail server went down at 3:40 in the morning and came back up around 6 am. We think the problem was a full disk on the backup partition. If you lost any messages during that time, please let us know and we will try to restore them from the tape.`,

  `Dnes jsem konečně dočetl knihu, kterou mi půjčil děda. Je to stará kniha o stavbě železnice přes hory a je v ní spousta map. Večer jsem pak s kamarády hrál na počítači a zítra jdeme na výlet na hrad. Kdo chce jet s námi, ať napíše do komentářů.`,

  `Today I finally installed the new version of Firefox and it is so much faster than the old browser. Tabs are great. I also started a blog, so this is my first post! I will write about music, school and maybe some programming. My favourite band right now is playing in Prague next month.`,

  `Diskuze: Má smysl kupovat nový mobil s fotoaparátem?
> Podle mě ne, fotky jsou pořád horší než z normálního foťáku.
Já ho mám měsíc a jsem spokojený. Na výlety to stačí a nemusím nosit dvě věci. Jen baterie vydrží sotva dva dny.`,

  `Abstract: We study the problem of learning word representations from large unlabeled text corpora. We propose a simple model that predicts the next word given the previous words and show that the learned representations improve results on several language tasks. Experiments on news data show that the model scales to millions of words.`,

  `The the the. Then there were three thinkers thinking that they thought the theory through. Other others gather together; whether the weather is better, nobody knows. 2005, 2006, 2007 – numbers too: 123 456 789.`,

  `Ahoj, posílám ti ty fotky z výletu. Jsou trochu velké, tak jsem je zabalil do zipu. Kdyby to nešlo otevřít, zkus jiný program. Měj se, Honza

P.S. Nezapomeň v pondělí na test z matiky!`,
];

export const SAMPLE_TEXT = SAMPLE_DOCS.join('\n\n');

export const DEMO_SENTENCE = 'Hi Kenneth, the meeting about the California energy contracts is moved to Friday at 10 am.';

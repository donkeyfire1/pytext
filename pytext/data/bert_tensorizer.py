#!/usr/bin/env python3
# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved

import itertools
from typing import List

from fairseq.data.legacy.masked_lm_dictionary import BertDictionary
from pytext.config.component import ComponentType, create_component
from pytext.data.tensorizers import Tensorizer, TokenTensorizer, lookup_tokens
from pytext.data.tokenizers import Gpt2Tokenizer, Tokenizer, WordPieceTokenizer
from pytext.data.utils import BOS, EOS, MASK, PAD, UNK, Vocabulary, pad_and_tensorize
from pytext.torchscript.tensorizer import ScriptRoBERTaTensorizer
from pytext.utils.torch import Vocabulary as ScriptVocabulary


class BERTTensorizer(TokenTensorizer):
    """
    Tensorizer for BERT tasks.  Works for single sentence, sentence pair, triples etc.
    """

    __EXPANSIBLE__ = True

    class Config(TokenTensorizer.Config):
        #: The tokenizer to use to split input text into tokens.
        columns: List[str] = ["text"]
        tokenizer: Tokenizer.Config = WordPieceTokenizer.Config()
        add_bos_token: bool = True
        add_eos_token: bool = True
        bos_token: str = "[CLS]"
        eos_token: str = "[SEP]"
        pad_token: str = "[PAD]"
        unk_token: str = "[UNK]"
        mask_token: str = "[MASK]"
        vocab_file: str = WordPieceTokenizer.Config().wordpiece_vocab_path

    @classmethod
    def from_config(cls, config: Config, **kwargs):
        tokenizer = create_component(ComponentType.TOKENIZER, config.tokenizer)
        replacements = {
            config.unk_token: UNK,
            config.pad_token: PAD,
            config.bos_token: BOS,
            config.eos_token: EOS,
            config.mask_token: MASK,
        }
        if isinstance(tokenizer, WordPieceTokenizer):
            vocab = Vocabulary(
                [token for token, _ in tokenizer.vocab.items()],
                replacements=replacements,
            )
        else:
            dictionary = BertDictionary.load(config.vocab_file)
            vocab = Vocabulary(
                dictionary.symbols, dictionary.count, replacements=replacements
            )
        return cls(
            columns=config.columns,
            tokenizer=tokenizer,
            add_bos_token=config.add_bos_token,
            add_eos_token=config.add_eos_token,
            use_eos_token_for_bos=config.use_eos_token_for_bos,
            max_seq_len=config.max_seq_len,
            vocab=vocab,
            **kwargs,
        )

    def __init__(self, columns, **kwargs):
        super().__init__(text_column=None, **kwargs)
        self.columns = columns
        # Manually initialize column_schema since we are sending None to TokenTensorizer

    def initialize(self, vocab_builder=None, from_scratch=True):
        # vocab for BERT is already set
        return
        # we need yield here to make this function a generator
        yield

    @property
    def column_schema(self):
        return [(column, str) for column in self.columns]

    def _lookup_tokens(self, text):
        return lookup_tokens(
            text,
            tokenizer=self.tokenizer,
            vocab=self.vocab,
            bos_token=None,
            eos_token=self.vocab.eos_token,
            max_seq_len=self.max_seq_len,
        )

    def numberize(self, row):
        """Tokenize, look up in vocabulary."""
        sentences = [self._lookup_tokens(row[column])[0] for column in self.columns]
        if self.add_bos_token:
            bos_token = (
                self.vocab.eos_token
                if self.use_eos_token_for_bos
                else self.vocab.bos_token
            )
            sentences[0] = [self.vocab.idx[bos_token]] + sentences[0]
        seq_lens = (len(sentence) for sentence in sentences)
        segment_labels = ([i] * seq_len for i, seq_len in enumerate(seq_lens))
        tokens = list(itertools.chain(*sentences))
        segment_labels = list(itertools.chain(*segment_labels))
        seq_len = len(tokens)
        # tokens, segment_label, seq_len
        return tokens, segment_labels, seq_len

    def sort_key(self, row):
        return row[2]

    def tensorize(self, batch):
        tokens, segment_labels, seq_lens = zip(*batch)
        tokens = pad_and_tensorize(tokens, self.vocab.get_pad_index())
        pad_mask = (tokens != self.vocab.get_pad_index()).long()
        segment_labels = pad_and_tensorize(segment_labels, self.vocab.get_pad_index())
        return tokens, pad_mask, segment_labels


class RoBERTaTensorizer(BERTTensorizer):
    class Config(Tensorizer.Config):
        columns: List[str] = ["text"]
        tokenizer: Gpt2Tokenizer.Config = Gpt2Tokenizer.Config()
        max_seq_len: int = 256

    @classmethod
    def from_config(cls, config: Config, **kwargs):
        tokenizer = create_component(ComponentType.TOKENIZER, config.tokenizer)
        vocab = tokenizer.vocab
        return cls(
            columns=config.columns,
            tokenizer=tokenizer,
            max_seq_len=config.max_seq_len,
            vocab=vocab,
        )

    def __init__(self, columns, tokenizer=None, vocab=None, max_seq_len=256):
        super().__init__(
            columns=columns,
            tokenizer=tokenizer,
            add_bos_token=False,
            add_eos_token=True,
            max_seq_len=max_seq_len,
            vocab=vocab,
        )
        self.bpe = self.tokenizer.bpe
        self.bos = self.tokenizer.bos
        self.eos = self.tokenizer.eos

    def _lookup_tokens(self, text):
        return lookup_tokens(
            text,
            tokenizer=self.tokenizer,
            vocab=self.vocab,
            bos_token=self.bos,
            eos_token=self.eos,
            max_seq_len=self.max_seq_len,
        )

    def torchscriptify(self):
        return ScriptRoBERTaTensorizer(
            tokenizer=self.tokenizer.torchscriptify(),
            vocab=ScriptVocabulary(
                list(self.vocab),
                pad_idx=self.vocab.get_pad_index(),
                bos_idx=self.vocab.get_bos_index(),
                eos_idx=self.vocab.get_eos_index(),
            ),
            max_seq_len=self.max_seq_len,
            add_bos_token=True,
            use_eos_token_for_bos=False,
        )

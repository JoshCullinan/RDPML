# 07 Jan 22
# Classifier finds the optimal minor and major parent for recombinant.

import argparse
import collections
import os
from pathlib import Path
import pandas as pd
import numpy as np
import math
from collections import defaultdict
import ast
from Bio import SeqIO, AlignIO
import distance
from intervaltree import Interval, IntervalTree
import re
import itertools

class classifier:

    def __init__(self, alig, rec, seq):        
        # Recombination events and sequence events files
        self.alignment = dict
        self.rec_events = pd.DataFrame
        self.seq_events = pd.DataFrame
        
        # The longest genome in the alignment files.
        self.maxGenomeLength = 0
        self.numberOfSeqs = 0

        # Generation matrix is a numpy array that is [number of alignments x max genome length] ([row x columns])
        self.generationMatrix = np.array

        # Dictionaries
        self.seqmap_dict = dict
        self.events_dict = dict
        self.events_map = dict
        self.inv_seqmap_dict = defaultdict(set)

        # Gap dict
        self.gaps = defaultdict(set)

        # Get relavent files that will be used in the parsing
        self.alignment_path = Path(alig)
        self.rec_events_path = Path(rec)
        self.seq_events_path = Path(seq)        
        self.major_parents = {}
        self.minor_parents = {}

        # Read in files function
        self.readFiles()
        # Create dictionaries used in generation matrix
        self.create_dictionaries()
        # Find posistion of Gap characters in the sequences
        self.getGaps()
        # Create generation count matrix
        self.createGenerationMatrix()
        # Calc Parents
        self.calcParents()
        # Output to csv.
        self.output()


    def readFiles(self):
        # JOSH: Trying the AlignIO feature from BioPython as they have get max length and number of Seq Fnc.
        # Useful if faster than my function for this.
        self.alignment = AlignIO.read(self.alignment_path, 'fasta')
        self.maxGenomeLength = self.alignment.get_alignment_length()
        self.numberOfSeqs = self.alignment.__len__()
        self.alignment = SeqIO.to_dict(self.alignment)
        #self.alignment = SeqIO.to_dict(SeqIO.parse(self.alignment_path, 'fasta'))

        #changing to just store sequence, dont need other entries that biopython stores in dictionary        
        for k, v in self.alignment.items():
            self.alignment[k] = v.seq

        # Read in Recombination events file
        self.rec_events = pd.read_csv(
            self.rec_events_path,
            sep=r"*",
            usecols=["EventNum", "Breakpoints", "Generation"],
        )

        # Remove the brackets surrounding breakpoints
        self.rec_events.Breakpoints = self.rec_events.Breakpoints.str.strip("[]")

        # Split breakpoints into start and end, drop breakpoints
        self.rec_events[["Start", "End"]] = self.rec_events.Breakpoints.str.split(",", expand=True,)

        # Read in sequence events map
        self.seq_events = pd.read_csv(self.seq_events_path, delimiter="*", index_col="Sequence")

    def create_dictionaries(self):
        # generating dictionaries from dataframes
        self.seqmap_dict = {
            i: ast.literal_eval(v)
            for i, v in enumerate(self.seq_events["Events"].to_numpy(), 1)
        }
        self.events_dict = {
            event: ast.literal_eval(bp)
            for event, bp in zip(
                self.rec_events["EventNum"], self.rec_events["Breakpoints"]
            )
        }
        # Creating an inverted seqmap dictionary (event:sequences instead of sequence:events)
        # with key,value pairs: event, [sequences containing event]
        for key, value in self.seqmap_dict.items():
            for eventnum in value:
                self.inv_seqmap_dict[eventnum].add(key)

    def getGaps(self):
        for key, seq in self.alignment.items():
            currentGaps = ([pos for pos, char in enumerate(str(seq)) if char == '-'])
            self.gaps[int(key)] = currentGaps

    def createGenerationMatrix(self):
        # Generation matrix is a numpy array that is [number of alignments x max genome length] ([row x columns])

        #self.generationMatrix = np.zeros(shape=(self.numberOfSeqs, self.maxGenomeLength))
        self.generationMatrix = np.full(shape=(self.numberOfSeqs, self.maxGenomeLength), dtype='O', fill_value=0)
        for key, gaps in self.gaps.items():
            self.generationMatrix[key-1, [pos for pos in gaps]] = '-'

        for event, seqs in self.inv_seqmap_dict.items():
            
            start = int(self.rec_events.Start[self.rec_events.EventNum == int(event)])
            end = int(self.rec_events.End[self.rec_events.EventNum == int(event)])

            # Fixes indexing error involving maximum genome length
            fix = 1
            if end == self.maxGenomeLength:
                fix = 0

            box = []
            for x in iter(seqs):
                box.append(x - 1)

            self.generationMatrix[box, start : (end + 1)] = np.full(
                (len(seqs), end + 1 * fix - start), int(event), dtype=np.int32
                )

    def calcHammingDistance(self, seq1, seq2):
        
        # Set for instances where seq1 and seq2 are 0 after dropping -
        normalisedDistance = None

        # Find the index of the gap characters in both sequences
        seq1Gaps = ({pos for pos, char in enumerate(str(seq1)) if char == '-'})
        seq2Gaps = ({pos for pos, char in enumerate(str(seq2)) if char == '-'})

        # Find the union of these Gap characters
        union = seq1Gaps | seq2Gaps

        # Remove the characters at the indexes of the union. 
        # 'A-TT-G' (Gaps at 1 and 4)
        # 'GG-TAA' (Gap at 3)
        # Union is {1,3,4}
        # Output sequences will be 'ATG' and 'GTA'. Hamming distance will be 2. 
        # Normalised over the total sites where neither sequence has a gap (3)
        # 2/3 = 0.66

        for count, indx in enumerate(union):
            seq1 = seq1[:indx-count] + seq1[indx+1-count:]
            seq2 = seq2[:indx-count] + seq2[indx+1-count:]
        
        #returns the hamming distance between two sequences
        d = distance.hamming(seq1, seq2)

        #Length of seqs
        totalNucleotides = len(seq1)

        if totalNucleotides > 0:
            # Normalise over number of chars without gap characters
            normalisedDistance = d/totalNucleotides

        return normalisedDistance

    def findEventPositions(self):
        #we need to know where the "recombination event blocks" are, i.e. which sections of the alignment we need to compare sequences within to find parents
        #these sections are different for every event, and arent neccesarily continuous: these blocks can be overwritten by later events, creating fragmented blocks

        #will make a dictionary to store this information
        #format is as follows {key = event number, value = dictionary of sequences}
        #where the dictionary of sequences stores the nucleotide positions for each sequence
        #for example if positions 100-200 for sequence 5 and 10 are written as event 2543 in the generation matrix, the dictionary entry will be as follows:
        #{2543: {5: [100, 200], 10: [100, 200]}}

        #The point is to have a way to efficiently know where any given recombination block in the generation matrix is, 
        #without having to search it for all entries of a given event number
        block_dict = {x:{} for x in self.events_dict.keys()}       

        #extracting raw array
        gen_matrix = self.generationMatrix

        #iterate through generation matrix, where index = a tuple (sequence name, nucleotide position) 
        for index, entry in np.ndenumerate(gen_matrix):
            #entry of 0 means no recombination event touched this nucleotide, so we don't have to do anything
            if not entry == 0:
                seq = index[0]
                nucleotide_pos = index[1]
                
                if seq in block_dict[entry]:
                    #if entry exists, change as appropiate
                    min_max = block_dict[entry][seq]
                    max_pos = min_max[-1][-1]                    

                    if nucleotide_pos == max_pos:
                        #continuous, uninterrupted block of same event, thus just add +1 to maximum range
                        min_max[-1][-1] += 1
                        block_dict[entry][seq] = min_max
                    else:
                        #discontinuity, so need to make a new range of nucleotides
                        min_max.append([nucleotide_pos, nucleotide_pos+1])
                        block_dict[entry][seq] = min_max
                else:   
                    #if dictionary entry doesnt exist yet, create a new one          
                    block_dict[entry][seq] = [[nucleotide_pos, nucleotide_pos+1]]

        return block_dict

    def findBestParentPair(self, minor_parent_dict, major_parent_dict):
        #given two lists: one list of potential minor parents and one list of potential major parents, this function returns the best parent pair
        #the "best" pair meets the following two conditions, if X is the region inherited from minor parent and Y from the major parent

        #1) in X: distance between recombinant and minor parent is minimised while distance between recombinant and major parent is maximised
        #2) in Y: distance between recombinant and major parent is minimised while distance between recombinant and minor parent is maximised

        #all possible pairs:
        all_pairs = list(itertools.product(minor_parent_dict.items(), major_parent_dict.items()))
        min_score = float('inf')
        best_pair = ()
       
        for pair in all_pairs:            
            #condition 1
            distance_X_minor = pair[0][1] if pair[0][1]!=None else 1
            distance_X_major = minor_parent_dict[pair[1][0]] if minor_parent_dict[pair[1][0]]!=None else 0
            
            sum1 = distance_X_minor + (1-distance_X_major)
            #condition 2
            distance_Y_major = pair[1][1] if pair[1][1]!=None else 1
            distance_Y_minor = major_parent_dict[pair[0][0]] if major_parent_dict[pair[0][0]]!=None else 0
            
            sum2 = distance_Y_major + (1-distance_Y_minor)

            pair_score = sum1+sum2
            if pair_score < min_score and pair_score < 4:
                min_score = pair_score
                best_pair = (pair[0][0], pair[1][0])  

        return (best_pair, min_score)

    def findHammingDistances(self, parent, ranges, deleted_nucleotides, recombinant_seq):
        #finds hamming distance, for both minor and major parent regions, between recombinant and potential parent
        parent_seq = str(self.alignment[str(parent+1)])        

        #if we do minor parents, take recombinant region and then remove intervals that have been deleted        
        ranges_tree_minor = IntervalTree.from_tuples(ranges)
        if parent in deleted_nucleotides.keys():                                               
            for j in deleted_nucleotides[parent]:
                ranges_tree_minor.chop(j.begin, j.end)  

        #for major parents, its the same except we take the complement of the recombinant region (all regions not in the recombinant region)        
        recombinant_region = IntervalTree.from_tuples(ranges)
        ranges_tree_major = IntervalTree.from_tuples([[0, self.maxGenomeLength]])
        for r in recombinant_region:
            ranges_tree_major.chop(r.begin, r.end)
        if parent in deleted_nucleotides.keys():                                               
            for j in deleted_nucleotides[parent]:
                ranges_tree_major.chop(j.begin, j.end) 
        
        #can extract sequences for both regions now, then calculating hamming distance
        seq1 = '' 
        seq2 = ''
        for k in ranges_tree_minor:
            seq1 = seq1 + recombinant_seq[k.begin:k.end]
            seq2 = seq2 + parent_seq[k.begin:k.end]

        #calculating hamming distance for minor parent
        hamming_distance_minor = self.calcHammingDistance(seq1, seq2)

        seq1 = '' 
        seq2 = ''
        for k in ranges_tree_major:
            seq1 = seq1 + recombinant_seq[k.begin:k.end]
            seq2 = seq2 + parent_seq[k.begin:k.end]

        #calculating hamming distance
        hamming_distance_major = self.calcHammingDistance(seq1, seq2)

        return (hamming_distance_minor, hamming_distance_major)

    def calculateParents(self, block_dict):                  
        #calculates "best" minor and major parents
    
        #this variable (deleted_nucleotides) will keep track of nucleotides with higher event numbers than all current events under consideration,
        #thus if a nucleotide falls into this range, it shouldnt be considered for parent calculations
        #Format is {sequence_name: interval_tree}
        #interval tree holds all intervals of deleted nucleotides
        deleted_nucleotides = {}
        parents_minor = {}            
        parents_major = {} 
        #we traverse block_dict in reverse order, calculating parents and then adding the ranges traversed to deleted nucleotides
        #i.e. we start at the highest event number, calculate parents with the recombinant region,
        #then that recombinant region is added to deleted nucleotides. Since all future events will have a smaller event number (earlier generation),
        #these nucleotides shouldnt be considered for any parent calculations.
        for event_number, sequence_ranges_dict in reversed(block_dict.items()):

            #divide sequences into recombinant and potential parents
            sequences_in_block = set(sequence_ranges_dict.keys())
            sequences_not_in_block = set(range(self.numberOfSeqs)) - sequences_in_block      
            best_parents_minor = []
            best_parents_major = []

            #calculate best parents for all sequences of the current recombination event
            for sequence, ranges in sequence_ranges_dict.items():  
                recombinant_seq = str(self.alignment[str(sequence+1)])  
                hamming_distances_major = {}
                hamming_distances_minor = {}               

                #calculate hamming distances for all potential parents
                for parent in sequences_not_in_block:
                    hamming_distance_both = self.findHammingDistances(parent, ranges, deleted_nucleotides, recombinant_seq)    
                    hamming_distances_minor[parent] = hamming_distance_both[0]                   
                    hamming_distances_major[parent] = hamming_distance_both[1]  

                #find best parents:
                best_parents_score = self.findBestParentPair(hamming_distances_minor, hamming_distances_major) 
                best_parents = best_parents_score[0]
                best_score = best_parents_score[1]           

                best_parents_minor.append((sequence+1, best_parents[0]+1, best_score))
                best_parents_major.append((sequence+1, best_parents[1]+1, best_score))
                '''
                #now all the distances have been calculated for this particular sequence, need to find minimum
                #if hamming distances is empty it means no best parent can be calculated for this event 
                if hamming_distances_major:               
                    minimum_seq = min(hamming_distances_major, key=hamming_distances_major.get)
                    #add this minimum hamming distance sequence, together with its hamming distance to best parents list 
                    #so this output is: recombinant sequence, best parent, hamming distance               
                    best_parents_major.append((sequence+1, minimum_seq+1, hamming_distances_major[minimum_seq]))
                else:
                    best_parents_major.append((sequence+1, None, None))

                if hamming_distances_minor:               
                    minimum_seq = min(hamming_distances_minor, key=hamming_distances_minor.get)
                    #add this minimum hamming distance sequence, together with its hamming distance to best parents list 
                    #so this output is: recombinant sequence, best parent, hamming distance               
                    best_parents_minor.append((sequence+1, minimum_seq+1, hamming_distances_minor[minimum_seq]))
                else:
                    best_parents_minor.append((sequence+1, None, None))
                '''

                #now add the nucleotides we have traversed to deleted nucleotides, these won't be considered in future events
                if sequence in deleted_nucleotides.keys():                                                            
                    deleted_nucleotides[sequence] = deleted_nucleotides[sequence].union(IntervalTree.from_tuples(ranges))                     
                    deleted_nucleotides[sequence].merge_overlaps()
                else:
                    deleted_nucleotides[sequence] = IntervalTree.from_tuples(ranges)

            parents_minor[event_number] = best_parents_minor
            parents_major[event_number] = best_parents_major  

        self.minor_parents = parents_minor
        self.major_parents = parents_major        

    def calcParents(self):
        #This function uses the generation matrix to calculate the best minor and major parents for each recombination event

        #we need to know where the "recombination event blocks" are, i.e. which sections of the alignment to compare to find parents
        #will make a dictionary to store this information, see function for more details on dictionary
        block_dict = self.findEventPositions()         
        #now we can use this dictionary to find the major parents
        print("Calculating best minor and major parents...")
        self.calculateParents(block_dict)  
        print("Done")       

    def output(self):
        print("Creating output file...")
        
        # Create unique key for the file name
        key = re.search(r'(?<=alignment_).*', self.alignment_path.name).group()[:-3]
        fileName = ("output/RPD_Output_" + key + '.rdp5ML')
        filePath = Path(fileName)
        
        try:
            os.makedirs('output')
        except FileExistsError:
            pass

        with open(fileName, "w") as g:
            header = ['SantaEventNumber', 'StartBP', 'EndBP', 'Recombinant', 'MinorParent', 'MajorParent', 'Score'] 
            g.write('\t'.join(str(s) for s in header) + '\n')

        for events in self.minor_parents.keys():
            startBP, EndBP = self.events_dict[events]
            for minorTup, MajorTup in zip(self.minor_parents[events], self.major_parents[events]):
                recom = minorTup[0]
                minor = minorTup[1]
                major = MajorTup[1]
                score = minorTup[2]
                
                with open(fileName, "a") as f:
                    content = [events, startBP, EndBP, recom, minor, major, score]
                    f.write('\t'.join(str(s) for s in content) + '\n')
        print('Done')

#### Unused ####
# def getFilePaths():
#     # This function is used to get the folder path from command line.
#     # For future pipeline use.

#     # Define command line argument parser
#     parser = argparse.ArgumentParser(
#         description="Parse Recombination Information from SantaSim"
#     )

#     # Add arguments for command line
#     parser.add_argument(
#         "-a",
#         dest="alignment_path",
#         type=str,
#         help="recombination events file",
#         required=True,
#     )
#     parser.add_argument(
#         "-f",
#         dest="recombination_path",
#         type=str,
#         help="recombination events file",
#         required=True,
#     )
#     parser.add_argument(
#         "-f",
#         dest="sequence_path",
#         type=str,
#         help="sequence events map file",
#         required=True,
#     )

#     # Parse Events
#     args = parser.parse_args()

#     # Returns the root folder to parse
#     return (args.alignment_path, args.recombination_path, args.sequence_path)


if __name__ == "__main__":

    # Using the file as a class from the module in the pipeline script. 
    # This section isn't necessary but leaving in for now. 
    print('Im main')

    # The line below will be used to get fileNames from pipeline later
    # alignment_path, recombination_path, sequence_path = getFilePaths()

    # # Currently used for testing purposes.
    # alignment_path = "data/alignment_XML5-4000-0.02-12E-5-50-4-3.fa"
    # recombination_path = "data/recombination_events_XML5-4000-0.02-12E-5-50-4-3.txt"
    # sequence_path = "data/sequence_events_map_XML5-4000-0.02-12E-5-50-4-3.txt"

    # # Create classifier class by initialising file paths
    # parser = classifier(alignment_path, recombination_path, sequence_path)

    # XML1-2500-0.01-12E-5-100-13
    # XML5-4000-0.02-12E-5-50-4-3
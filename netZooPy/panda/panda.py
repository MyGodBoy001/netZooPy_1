from __future__ import print_function

import math
import time
import pandas as pd
from scipy.stats import zscore
from .timer import Timer
import numpy as np

class Panda(object):
    """ 
    Description:
        Using PANDA to infer gene regulatory network.
        1. Reading in input data (expression data, motif prior, TF PPI data)
        2. Computing coexpression network
        3. Normalizing networks
        4. Running PANDA algorithm
        5. Writing out PANDA network

    Inputs:
        object: Panda object.

    Outputs:
        object: Panda result object
        object.panda_network: adjacency matrix of resulting network
                      
     Methods:
        __init__                    : Intialize instance of Panda class.
        __remove_missing            : Removes the gens and TFs that are not present in one of the priors. Works only if modeProcess='legacy'.
        _normalize_network          : Standardizes the input data matrices.
        processData                 : Processes data files into data matrices.
        panda_loop                  : The PANDA algorithm.
        __pearson_results_data_frame: Saves PANDA network in edges format.
        save_panda_results          : Saves PANDA network.
        top_network_plot            : Selects top genes to plot.
        __shape_plot_network        : Creates network plot.
        __create_plot               : Runs network plot.
        return_panda_indegree       : computes indegree of panda network, only if save_memory = False.
        return_panda_outdegree      : computes outdegree of panda network, only if save_memory = False.

    Example:
        Import the classes in the pypanda library:
        from netZooPy.panda.panda import Panda
        Run the Panda algorithm, leave out motif and PPI data to use Pearson correlation network:
        panda_obj = Panda('../../tests/ToyData/ToyExpressionData.txt', '../../tests/ToyData/ToyMotifData.txt', '../../tests/ToyData/ToyPPIData.txt', remove_missing=False)
        Save the results:
        panda_obj.save_panda_results('Toy_Panda.pairs.txt')
        Return a network plot:
        panda_obj.top_network_plot(top=70, file='top_genes.png')
        Calculate in- and outdegrees for further analysis:
        indegree = panda_obj.return_panda_indegree()
        outdegree = panda_obj.return_panda_outdegree()
        Toy data:
        The example gene expression data that we have available here contains gene expression profiles for different samples in the columns. Of note, this is just a small subset of a larger gene expression dataset. We provided these "toy" data so that the user can test the method. 
        However, if you plan to model gene regulatory networks on your own dataset, you should use your own expression data as input.
        Sample PANDA results:
        TF  Gene  Motif Force
        ---------------------
        CEBPA	AACSL	0.0	-0.951416589143
        CREB1	AACSL	0.0	-0.904241609324
        DDIT3	AACSL	0.0	-0.956471642313
        E2F1	AACSL	1.0	3.6853160511
        EGR1	AACSL	0.0	-0.695698519643

     Authors: 
       Cho-Yi Chen, David Vi, Alessandro Marin, Marouen Ben Guebila, Daniel Morgan

    Reference:
        Glass, Kimberly, et al. "Passing messages between biological networks to refine predicted interactions." PloS one 8.5 (2013): e64832.
    """
    def __init__(self, expression_file, motif_file, ppi_file, computing='cpu',precision='double',save_memory = True, save_tmp=True, remove_missing=False, keep_expression_matrix = False, modeProcess = 'union', alpha = 0.1):
        """ 
        Description:
            Intialize instance of Panda class and load data.

        Inputs:
            expression_file : Path to file containing the gene expression data or pandas dataframe.
            motif_file      : Path to file containing the transcription factor DNA binding motif data in the form of TF-gene-weight(0/1) or pandas dataframe.
                              If set to none, the gene coexpression matrix is returned as a result network.
            ppi_file        : Path to file containing the PPI data. or pandas dataframe. The PPI can be symmetrical, if not, it will be transformed into a symmetrical adjacency matrix.
            computing       : 'cpu' uses Central Processing Unit (CPU) to run PANDA.
                              'gpu' use the Graphical Processing Unit (GPU) to run PANDA.
            precision       : 'double' computes the regulatory network in double precision (15 decimal digits).
                              'single' computes the regulatory network in single precision (7 decimal digits) which is fastaer, requires half the memory but less accurate.
            save_memory     : True : removes temporary results from memory. The result network is weighted adjacency matrix of size (nTFs, nGenes).
                              False: keeps the temporary files in memory. The result network has 4 columns in the form gene - TF - weight in motif prior - PANDA edge.
            save_tmp        : Save temporary variables.
            remove_missing  : Removes the gens and TFs that are not present in one of the priors. Works only if modeProcess='legacy'.
            keep_expression_matrix: Keeps the input expression matrix in the result Panda object.
            modeProcess     : The input data processing mode.
                              'legacy': refers to the processing mode in netZooPy<=0.5
                              (Default)'union': takes the union of all TFs and genes across priors and fills the missing genes in the priors with zeros.
                              'intersection': intersects the input genes and TFs across priors and removes the missing TFs/genes.
            alpha           : Learning rate (default: 0.1)
        """
        # Read data
        self.processData(modeProcess, motif_file, expression_file, ppi_file, remove_missing, keep_expression_matrix)
        if hasattr(self, 'export_panda_results'):
            return
        
        # =====================================================================
        # Network normalization
        # =====================================================================

        with Timer('Normalizing networks ...'):
            self.correlation_matrix = self._normalize_network(self.correlation_matrix)
            with np.errstate(invalid='ignore'): #silly warning bothering people
                self.motif_matrix = self._normalize_network(self.motif_matrix_unnormalized)
            self.ppi_matrix = self._normalize_network(self.ppi_matrix)
            if precision=='single':
                self.correlation_matrix=np.float32(self.correlation_matrix)
                self.motif_matrix=np.float32(self.motif_matrix)
                self.ppi_matrix=np.float32(self.ppi_matrix)
        # =====================================================================
        # Clean up useless variables to release memory
        # =====================================================================
        self.tfs, self.genes = self.unique_tfs, self.gene_names
        if save_memory:
            print("Clearing motif and ppi data, unique tfs, and gene names for speed")
            del self.unique_tfs, self.gene_names, self.motif_matrix_unnormalized

        # =====================================================================
        # Saving middle data to tmp
        # =====================================================================
        if save_tmp:
            with Timer('Saving expression matrix and normalized networks ...'):
                if self.expression_data is not None:
                    np.save('/tmp/expression.npy', self.expression_data.values)
                np.save('/tmp/motif.normalized.npy', self.motif_matrix)
                np.save('/tmp/ppi.normalized.npy', self.ppi_matrix)

        # delete expression data
        del self.expression_data

        # =====================================================================
        # Running PANDA algorithm
        # =====================================================================
        if self.motif_data is not None:
            print('Running PANDA algorithm ...')
            self.panda_network = self.panda_loop(self.correlation_matrix, self.motif_matrix, self.ppi_matrix, computing, alpha)
            # label dataframe
            self.panda_network = pd.DataFrame(self.panda_network, index=self.tfs, columns=self.genes)
        else:
            self.panda_network = self.correlation_matrix
            self.__pearson_results_data_frame()
            # label dataframe
            self.panda_network = pd.DataFrame(self.panda_network, index=self.genes, columns=self.genes)

    def __remove_missing(self):
        """ 
        Description:
            Removes the gens and TFs that are not present in one of the priors. Works only if modeProcess='legacy'.
        """
        if self.expression_data is not None:
            print("Remove expression not in motif:")
            motif_unique_genes = set(self.motif_data[1])
            len_tot = len(self.expression_data)
            self.expression_data = self.expression_data[self.expression_data.index.isin(motif_unique_genes)]
            self.gene_names = self.expression_data.index.tolist()
            self.num_genes = len(self.gene_names)
            print("   {} rows removed from the initial {}".format(len_tot-self.num_genes,len_tot))
        #if self.motif_data is not None:
        print("Remove motif not in expression data:")
        len_tot = len(self.motif_data)
        self.motif_data = self.motif_data[self.motif_data.iloc[:,1].isin(self.gene_names)]
        self.unique_tfs = sorted(set(self.motif_data[0]))
        self.num_tfs = len(self.unique_tfs)
        print("   {} rows removed from the initial {}".format(len_tot-len(self.motif_data),len_tot))
        if self.ppi_data is not None:
            print("Remove ppi not in motif:")
            motif_unique_tfs = np.unique(self.motif_data.iloc[:,0])
            len_tot = len(self.ppi_data)
            self.ppi_data = self.ppi_data[self.ppi_data.iloc[:,0].isin(motif_unique_tfs)]
            self.ppi_data = self.ppi_data[self.ppi_data.iloc[:,1].isin(motif_unique_tfs)]
            print("   {} rows removed from the initial {}".format(len_tot-len(self.ppi_data),len_tot))
        return None

    def _normalize_network(self, x):
        """ 
        Description:
            Standardizes the input data matrices.

        Inputs:
            x     : Input adjacency matrix.

        Outputs:
            normalized_matrix: Standardized adjacency matrix.
        """
        norm_col = zscore(x, axis=0)
        if x.shape[0] == x.shape[1]:
            norm_row = norm_col.T
        else:
            norm_row = zscore(x, axis=1)
        #Alessandro: replace nan values
        normalized_matrix = (norm_col + norm_row) / math.sqrt(2)
        norm_total = (x-np.mean(x))/np.std(x)   #NB zscore(x) is not the same
        nan_col = np.isnan(norm_col)
        nan_row = np.isnan(norm_row)
        normalized_matrix[nan_col] = (norm_row[nan_col] + norm_total[nan_col])/math.sqrt(2)
        normalized_matrix[nan_row] = (norm_col[nan_row] + norm_total[nan_row])/math.sqrt(2)
        normalized_matrix[nan_col & nan_row] = 2*norm_total[nan_col & nan_row]/math.sqrt(2)
        return normalized_matrix

    def processData(self, modeProcess, motif_file, expression_file, ppi_file, remove_missing, keep_expression_matrix):
        """ 
        Description:
            Processes data files into data matrices.

        Inputs:
            modeProcess           : Input adjacency matrix.
            expression_file       : Path to file containing the gene expression data.
            motif_file            : Path to file containing the transcription factor DNA binding motif data in the form of TF-gene-weight(0/1).
                                    If set to none, the gene coexpression matrix is returned as a result network.
            ppi_file              : Path to file containing the PPI data.
            remove_missing  : Removes the gens and TFs that are not present in one of the priors. Works only if modeProcess='legacy'.
            keep_expression_matrix: Keeps the input expression matrix in the result Panda object.
        """
        # if modeProcess=="legacy":
        # =====================================================================
        # Data loading
        # =====================================================================
        if type(motif_file) is str:
            with Timer('Loading motif data ...'):
                self.motif_data = pd.read_csv(motif_file, sep='\t', header=None)
                self.motif_tfs = sorted(set(self.motif_data[0]))
                self.motif_genes = sorted(set(self.motif_data[1]))
                # self.num_tfs = len(self.unique_tfs)
                # print('Unique TFs:', self.num_tfs)
        elif type(motif_file) is not str:
            if motif_file is None:
                self.motif_data  = None
                self.motif_genes = []
                self.motif_tfs   = []
            else:
                if not isinstance(motif_file, pd.DataFrame):
                    raise Exception("Please provide a pandas dataframe for motif data with column names as 'source', 'target', and 'weight'.")
                if ('source' not in motif_file.columns) or ('target' not in motif_file.columns):
                    print('renaming motif columns to "source", "target" and "weight" ')
                    motif_file.columns = ['source','target','weight']
                self.motif_data = pd.DataFrame(motif_file.values) 
                self.motif_tfs  = sorted(set(motif_file['source']))
                self.motif_genes = sorted(set(motif_file['target']))
            # self.num_tfs = len(self.unique_tfs)
            # print('Unique TFs:', self.num_tfs)

        if type(expression_file) is str:
            with Timer('Loading expression data ...'):
                self.expression_data = pd.read_csv(expression_file, sep='\t', header=None, index_col=0)
                self.expression_genes = self.expression_data.index.tolist()
                # self.num_genes = len(self.gene_names)
                # print('Expression matrix:', self.expression_data.shape)
        elif type(expression_file) is not str:
            if expression_file is not None:
                if not isinstance(expression_file, pd.DataFrame):
                    raise Exception("Please provide a pandas dataframe for expression data.")
                self.expression_data = expression_file #pd.read_csv(expression_file, sep='\t', header=None, index_col=0)
                self.expression_genes = self.expression_data.index.tolist()
                # self.num_genes = len(self.gene_names)
                # print('Expression matrix:', self.expression_data.shape)
            else:
                self.gene_names       = self.motif_genes      
                self.expression_genes = self.motif_genes
                self.num_genes = len(self.gene_names)
                self.expression_data = None #pd.DataFrame(np.identity(self.num_genes, dtype=int))
                print('No Expression data given: correlation matrix will be an identity matrix of size', len(self.motif_genes))

        if len(self.expression_genes)!=len(np.unique(self.expression_genes)):
            print('Duplicate gene symbols detected. Consider averaging before running PANDA')

        if type(ppi_file) is str:
            with Timer('Loading PPI data ...'):
                self.ppi_data = pd.read_csv(ppi_file, sep='\t', header=None)
                self.ppi_tfs  = sorted(set(pd.concat([self.ppi_data[0],self.ppi_data[1]])))
                print('Number of PPIs:', self.ppi_data.shape[0])
        elif type(ppi_file) is not str:
            if ppi_file is not None:
                if not isinstance(ppi_file, pd.DataFrame):
                    raise Exception("Please provide a pandas dataframe for PPI data.")
                self.ppi_data = ppi_file #pd.read_csv(ppi_file, sep='\t', header=None)
                self.ppi_tfs  = sorted(set(pd.concat([self.ppi_data[0],self.ppi_data[1]])))
                print('Number of PPIs:', self.ppi_data.shape[0])
            else:
                print('No PPI data given: ppi matrix will be an identity matrix of size', len(self.motif_tfs))
                self.ppi_data = None
                self.ppi_tfs  = self.motif_tfs

        if modeProcess=="legacy" and remove_missing and motif_file is not None:
            self.__remove_missing()
        if modeProcess=="legacy":
            if expression_file is not None:
                self.gene_names = self.expression_genes #sorted( np.unique(self.motif_genes +  self.expression_genes ))
            if motif_file is None:
                self.unique_tfs = self.ppi_tfs
            else:
                self.unique_tfs = self.motif_tfs#sorted( np.unique(self.ppi_tfs     +  self.motif_tfs ))

        elif modeProcess=="union":
            self.gene_names = sorted( np.unique(self.motif_genes +  self.expression_genes ))
            self.unique_tfs = sorted( np.unique(self.ppi_tfs     +  self.motif_tfs ))

        elif modeProcess=="intersection":
            if motif_file is None:
                self.gene_names = sorted( np.unique(self.expression_genes ))
                self.unique_tfs = sorted( np.unique(self.ppi_tfs ))
            else:
                self.gene_names = sorted(np.unique( list(set(self.motif_genes).intersection(set(self.expression_genes))) ))
                self.unique_tfs = sorted(np.unique( list(set(self.ppi_tfs).intersection(set(self.motif_tfs)) )))
        
        self.num_genes  = len(self.gene_names)
        self.num_tfs    = len(self.unique_tfs)

        # Auxiliary dicts
        gene2idx = {x: i for i,x in enumerate(self.gene_names)}
        tf2idx = {x: i for i,x in enumerate(self.unique_tfs)}
        if (modeProcess=="union" or modeProcess=="intersection") and (self.expression_data is not None) and (self.num_genes!=0):
            # Initialize data & Populate gene expression
            self.expression = np.zeros((self.num_genes, self.expression_data.shape[1]))
            idx_geneEx = [gene2idx.get(x, 0) for x in self.expression_genes]
            self.expression[idx_geneEx,:] = self.expression_data.values
            self.expression_data=pd.DataFrame(data=self.expression, index=self.gene_names)

        # =====================================================================
        # Network construction
        # =====================================================================
        with Timer('Calculating coexpression network ...'):
            if self.expression_data is None:
                self.correlation_matrix = np.identity(self.num_genes,dtype=int)
            else:
                self.correlation_matrix = np.corrcoef(self.expression_data)
            if np.isnan(self.correlation_matrix).any():
                np.fill_diagonal(self.correlation_matrix, 1)
                self.correlation_matrix = np.nan_to_num(self.correlation_matrix)

        # Clean up useless variables to release memory
        if keep_expression_matrix:
            if self.expression_data is not None:
                self.expression_matrix = self.expression_data.values
            else:
                self.expression_matrix = None

        if self.motif_data is None:
            print('Returning the correlation matrix of expression data in <Panda_obj>.correlation_matrix')
            self.panda_network        = self.correlation_matrix
            self.export_panda_results = self.correlation_matrix
            self.motif_matrix         = self.motif_data
            self.ppi_matrix           = self.ppi_data
            self.__pearson_results_data_frame()
            self.panda_network = pd.DataFrame(self.panda_network, index=self.expression_genes, columns=self.expression_genes)
            return

        with Timer('Creating motif network ...'):
            self.motif_matrix_unnormalized = np.zeros((self.num_tfs, self.num_genes))
            idx_tfs = [tf2idx.get(x, 0) for x in self.motif_data[0]]
            idx_genes = [gene2idx.get(x, 0) for x in self.motif_data[1]]
            idx = np.ravel_multi_index((idx_tfs, idx_genes), self.motif_matrix_unnormalized.shape)
            self.motif_matrix_unnormalized.ravel()[idx] = self.motif_data[2]

        if self.ppi_data is None:
            self.ppi_matrix = np.identity(self.num_tfs,dtype=int)
        else:
            with Timer('Creating PPI network ...'):
                self.ppi_matrix = np.identity(self.num_tfs)
                idx_tf1 = [tf2idx.get(x, 0) for x in self.ppi_data[0]]
                idx_tf2 = [tf2idx.get(x, 0) for x in self.ppi_data[1]]
                idx = np.ravel_multi_index((idx_tf1, idx_tf2), self.ppi_matrix.shape)
                self.ppi_matrix.ravel()[idx] = self.ppi_data[2]
                idx = np.ravel_multi_index((idx_tf2, idx_tf1), self.ppi_matrix.shape)
                self.ppi_matrix.ravel()[idx] = self.ppi_data[2]
        
        return

    def panda_loop(self, correlation_matrix, motif_matrix, ppi_matrix, computing='cpu', alpha=0.1):
        """ 
        Description:
            The PANDA algorithm.

        Inputs:
            correlation_matrix: Input coexpression matrix.
            motif_matrix      : Input motif regulation prior network.
            ppi_matrix        : Input PPI matrix.
            computing         : 'cpu' uses Central Processing Unit (CPU) to run PANDA.
                                'gpu' use the Graphical Processing Unit (GPU) to run PANDA.

        Methods:
            t_function      : Continuous Tanimoto similarity function computed on the CPU.
            update_diagonal : Updates the diagonal of the input matrix in the message passing computed on the CPU.
            gt_function     : Continuous Tanimoto similarity function computed on the GPU.
            gupdate_diagonal: Updates the diagonal of the input matrix in the message passing computed on the GPU.
        """
        def t_function(x, y=None):
            """ 
            Description:
                Continuous Tanimoto similarity function computed on the CPU.

            Inputs:
                x: First object to measure the distance from. If only this matrix is provided, then the distance is meausred between the columns of x.
                y: Second object to measure the distance to.

            Ouputs:
                a_matrix: Matrix containing the pairwsie distances. 
            """
            if y is None:
                a_matrix = np.dot(x, x.T)
                s = np.square(x).sum(axis=1)
                a_matrix /= np.sqrt(s + s.reshape(-1, 1) - np.abs(a_matrix))
            else:
                a_matrix = np.dot(x, y)
                a_matrix /= np.sqrt(np.square(y).sum(axis=0) + np.square(x).sum(axis=1).reshape(-1, 1) - np.abs(a_matrix))
            return a_matrix

        def update_diagonal(diagonal_matrix, num, alpha, step):
            """ 
            Description:
                Updates the diagonal of the input matrix in the message passing computed on the CPU.

            Inputs:
                diagonal_matrix: Input diagonal matrix.
                num            : Number of rows/columns.
                alpha          : Learning rate.
                step           : The current step in the algorithm.
            """
            np.fill_diagonal(diagonal_matrix, np.nan)
            diagonal_std = np.nanstd(diagonal_matrix, 1)
            diagonal_fill = diagonal_std * num * math.exp(2 * alpha * step)
            np.fill_diagonal(diagonal_matrix, diagonal_fill)

        def gt_function(x, y=None):
            """ 
            Description:
                Continuous Tanimoto similarity function computed on the GPU.

            Inputs:
                x: First object to measure the distance from. If only this matrix is provided, then the distance is meausred between the columns of x.
                y: Second object to measure the distance to.

            Ouputs:
                a_matrix: Matrix containing the pairwsie distances. 
            """
            if y is None:
                a_matrix = cp.dot(x, x.T)
                s = cp.square(x).sum(axis=1)
                a_matrix /= cp.sqrt(s + s.reshape(-1, 1) - cp.abs(a_matrix))
            else:
                a_matrix = cp.dot(x, y)
                a_matrix /= cp.sqrt(cp.square(y).sum(axis=0) + cp.square(x).sum(axis=1).reshape(-1, 1) - cp.abs(a_matrix))
            return a_matrix

        def gupdate_diagonal(diagonal_matrix, num, alpha, step):
            """ 
            Description:
                Updates the diagonal of the input matrix in the message passing computed on the GPU.

            Inputs:
                diagonal_matrix: Input diagonal matrix.
                num            : Number of rows/columns.
                alpha          : Learning rate.
                step           : The current step in the algorithm.
            """
            cp.fill_diagonal(diagonal_matrix, cp.nan)
            diagonal_std = cp.nanstd(diagonal_matrix, 1)
            diagonal_fill = diagonal_std * num * math.exp(2 * alpha * step)
            cp.fill_diagonal(diagonal_matrix, diagonal_fill)

        panda_loop_time = time.time()
        num_tfs, num_genes = motif_matrix.shape
        step = 0
        hamming = 1
        
        while hamming > 0.001:
            # Update motif_matrix
            if computing=='gpu':
                import cupy as cp
                ppi_matrix=cp.array(ppi_matrix)
                motif_matrix=cp.array(motif_matrix)
                correlation_matrix=cp.array(correlation_matrix)
                W = 0.5 * (gt_function(ppi_matrix, motif_matrix) + gt_function(motif_matrix, correlation_matrix))  # W = (R + A) / 2
                hamming = cp.abs(motif_matrix - W).mean()
                motif_matrix=cp.array(motif_matrix)
                motif_matrix *= (1 - alpha)
                motif_matrix += (alpha * W)

                if hamming > 0.001:
                    # Update ppi_matrix
                    ppi = gt_function(motif_matrix)  # t_func(X, X.T)
                    gupdate_diagonal(ppi, num_tfs, alpha, step)
                    ppi_matrix *= (1 - alpha)
                    ppi_matrix += (alpha * ppi)

                    # Update correlation_matrix
                    motif = gt_function(motif_matrix.T)
                    gupdate_diagonal(motif, num_genes, alpha, step)
                    correlation_matrix *= (1 - alpha)
                    correlation_matrix += (alpha * motif)

                    del W, ppi, motif  # release memory for next step

            elif computing=='cpu':
                W = 0.5 * (t_function(ppi_matrix, motif_matrix) + t_function(motif_matrix, correlation_matrix))  # W = (R + A) / 2
                hamming = np.abs(motif_matrix - W).mean()
                motif_matrix *= (1 - alpha)
                motif_matrix += (alpha * W)

                if hamming > 0.001:
                    # Update ppi_matrix
                    ppi = t_function(motif_matrix)  # t_func(X, X.T)
                    update_diagonal(ppi, num_tfs, alpha, step)
                    ppi_matrix *= (1 - alpha)
                    ppi_matrix += (alpha * ppi)
                    
                    # Update correlation_matrix
                    motif = t_function(motif_matrix.T)
                    update_diagonal(motif, num_genes, alpha, step)
                    correlation_matrix *= (1 - alpha)
                    correlation_matrix += (alpha * motif)

                    del W, ppi, motif  # release memory for next step

            print('step: {}, hamming: {}'.format(step, hamming))
            step = step + 1

        print('Running panda took: %.2f seconds!' % (time.time() - panda_loop_time))
        #Ale: reintroducing the export_panda_results array if Panda called with save_memory=False
        if computing=='gpu':
            motif_matrix=cp.asnumpy(motif_matrix)
        if hasattr(self,'unique_tfs'):
            tfs = np.tile(self.unique_tfs, (len(self.gene_names), 1)).flatten()
            genes = np.repeat(self.gene_names,self.num_tfs)
            motif = self.motif_matrix_unnormalized.flatten(order='F')
            force = motif_matrix.flatten(order='F')
            self.export_panda_results = pd.DataFrame({'tf':tfs, 'gene': genes,'motif': motif, 'force': force})
            # self.export_panda_results = np.column_stack((tfs,genes,motif,force))
        return motif_matrix

    def __pearson_results_data_frame(self):
        """ 
        Description:
            Saves PANDA network in edges format.
        """
        genes_1 = np.tile(self.gene_names, (len(self.gene_names), 1)).flatten()
        genes_2 = np.tile(self.gene_names, (len(self.gene_names), 1)).transpose().flatten()
        self.flat_panda_network = self.panda_network.transpose().flatten()
        print(genes_1)
        print(genes_2)
        print((self.flat_panda_network).shape)
        self.export_panda_results = pd.DataFrame({'tf':genes_1, 'gene':genes_2, 'force':self.flat_panda_network})
        self.export_panda_results = self.export_panda_results[['tf', 'gene', 'force']]
        return None

    def save_panda_results(self, path='panda.npy'):
        """ 
        Description:
            Saves PANDA network.

        Inputs:
            path: Path to save the network.
        """
        with Timer('Saving PANDA network to %s ...' % path):
            #Because there are two modes of operation (save_memory), save to file will be different
            if not hasattr(self,'unique_tfs'):
                toexport = self.panda_network
            else:
                toexport = self.export_panda_results
            #Export to file
            if path.endswith('.txt'):
                np.savetxt(path, toexport,fmt='%s', delimiter=' ')
            elif path.endswith('.csv'):
                np.savetxt(path, toexport,fmt='%s', delimiter=',')
            elif path.endswith('.tsv'):
                np.savetxt(path, toexport,fmt='%s', delimiter='/t')
            else:
                np.save(path, toexport)

    def top_network_plot(self, top = 100, file = 'panda_top_100.png',plot_bipart=False):
        """
        Description:
            Selects top genes.

        Inputs:
            top        : Top number of genes to plot.
            file       : File to save the network plot.
            plot_bipart: Plot the network as a bipartite layout.
        """
        if not hasattr(self,'export_panda_results'):
            raise AttributeError("Panda object does not contain the export_panda_results attribute.\n"+
                "Run Panda with the flag save_memory=False")
        #Ale TODO: work in numpy instead of pandas?
        self.panda_results = pd.DataFrame(self.export_panda_results, columns=['tf','gene','motif','force'])
        subset_panda_results = self.panda_results.sort_values(by=['force'], ascending=False)
        subset_panda_results = subset_panda_results[subset_panda_results.tf != subset_panda_results.gene]
        subset_panda_results = subset_panda_results[0:top]
        self.__shape_plot_network(subset_panda_results = subset_panda_results, file = file, plot_bipart=plot_bipart)
        return None

    def __shape_plot_network(self, subset_panda_results, file = 'panda.png',plot_bipart=False):
        """
        Description:
            Creates plot.

        Inputs:
            subset_panda_results : Reduced PANDA network to the top genes.
            file                 : File to save the network plot.
            plot_bipart: Plot the network as a bipartite layout.
        """
        #reshape data for networkx
        unique_genes = list(set(list(subset_panda_results['tf'])+list(subset_panda_results['gene'])))
        unique_genes = pd.DataFrame(unique_genes)
        unique_genes.columns = ['name']
        unique_genes['index'] = unique_genes.index
        subset_panda_results = subset_panda_results.merge(unique_genes, how='inner', left_on='tf', right_on='name')
        subset_panda_results = subset_panda_results.rename(columns = {'index': 'tf_index'})
        subset_panda_results = subset_panda_results.drop(['name'], 1)
        subset_panda_results = subset_panda_results.merge(unique_genes, how='inner', left_on='gene', right_on='name')
        subset_panda_results = subset_panda_results.rename(columns = {'index': 'gene_index'})
        subset_panda_results = subset_panda_results.drop(['name'], 1)
        links = subset_panda_results[['tf_index', 'gene_index', 'force']]
        self.__create_plot(unique_genes = unique_genes, links = links, file = file,plot_bipart=plot_bipart)
        return None

    def __create_plot(self, unique_genes, links, file = 'panda.png',plot_bipart=False):
        """
        Description:
            Runs the plot.

        Inputs:
            unique_genes : Unique list of PANDA genes.
            links        : Edges of the subset PANDA network to the top genes.
            file         : File to save the network plot.
            plot_bipart  : Plot the network as a bipartite layout.

        Methods:
            split_label: Splits the plot label over several lines for plotting purposes.
        """
        import networkx as nx
        import matplotlib.pyplot as plt
        g = nx.Graph()
        g.clear()
        plt.clf()
        #img = plt.imread("../img/panda.jpg")
        #fig, ax = plt.subplots()
        #ax.imshow(img, extent=[0, 400, 0, 300])
        ##ax.plot(x, x, '--', linewidth=5, color='firebrick')
        g.add_nodes_from(unique_genes['index'])
        edges = []
        for i in range(0, len(links)):
            edges = edges + [(links.iloc[i]['tf_index'], links.iloc[i]['gene_index'], float(links.iloc[i]['force'])/200)]
        g.add_weighted_edges_from(edges)
        labels = {}

        def split_label(label):
            """
            Description: Splits the plot label over several lines for plotting purposes.

            Inputs:    
                label: Input label text.

            Outputs:
                label: Output label text divided over several lines.
            """
            ll = len(label)
            if ll > 6:
                return label[0:int(np.ceil(ll/2))] + '\n' + label[int(np.ceil(ll/2)):]
            return label

        for i, l in enumerate(unique_genes.iloc[:,0]):
            labels[i] = split_label(l)
        if not plot_bipart:
            pos = nx.spring_layout(g)
        else:
            pos = nx.drawing.layout.bipartite_layout(g, set(links['tf_index']))
        #nx.draw_networkx(g, pos, labels=labels, node_size=40, font_size=3, alpha=0.3, linewidth = 0.5, width =0.5)
        print(plot_bipart)
        if not plot_bipart:
            colors=range(len(edges))
        else:
            colors=list(zip(*edges))[-1]
                                                     
        options = {'alpha': 0.7, 'edge_color': colors, 'edge_cmap': plt.cm.Blues, 'node_size' :110, 'vmin': -100,
                   'width': 2, 'labels': labels, 'font_weight': 'regular', 'font_size': 3, 'linewidths': 20}
        
        nx.draw_networkx(g, pos=pos,**options)
        plt.axis('off')
        plt.savefig(file, dpi=300)
        return None

    def return_panda_indegree(self):
        """
        Description:
            computes indegree of PANDA network, only if save_memory = False.
        """
        export_panda_results_pd = pd.DataFrame(self.export_panda_results,columns=['tf','gene','motif','force'])
        subset_indegree = export_panda_results_pd.loc[:,['gene','force']]
        self.panda_indegree = subset_indegree.groupby('gene').sum()
        return self.panda_indegree

    def return_panda_outdegree(self):
        """
        Description:
            computes outdegree of PANDA network, only if save_memory = False.
        """
        export_panda_results_pd = pd.DataFrame(self.export_panda_results,columns=['tf','gene','motif','force'])
        subset_outdegree = export_panda_results_pd.loc[:,['tf','force']]
        self.panda_outdegree = subset_outdegree.groupby('tf').sum()
        return self.panda_outdegree 
